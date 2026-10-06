"""CV tailoring orchestrator (#364/#365/#368): select -> bundle -> compose -> read the reply
-> select from it -> check -> assemble -> audit -> render -> serve -> record -> notify.

Composition is a bounded backend call over the closed verified bundle, and its reply is DATA:
a JSON object holding the profile, cited bullets per role slot and skill picks from a closed
list. sluice reads it (cv/reply.py), selects what may render (cv/selection.py), checks the
selection (cv/validate.py::check_selection), and assembles the CV itself from the CV Layout
and the Candidate Profile (cv/document.py): the model never writes a heading, a date, a
name, a certificate or an education line.

The gate has two tiers, over the MODEL's text only -- vault text is the user's. A HARD one:
the reply's shape, the fabrication checks over the selection, and an em dash or a literal
`--`. A SCOPED STYLE one (#167): slop phrases and unbundled terms (#194). Either drives
exactly one retry with the findings, and the previous reply's drops, fed back; the loop
RETAINS the hard-clean attempt with the fewest style/voice findings (a tie keeps the later
attempt, and one whose voice check failed never displaces one whose voice was measured). So a
lead is skipped -- never rendered ungated -- when no attempt ever cleared the hard tier, and
never merely over a phrase. dry_run computes and reports, and writes nothing to the store,
the renderer or served_dir.

It DOES write the run's diagnostic artefacts (cv/artefacts.py), and that is decided rather
than inherited: a dry run already spends a composition and an audit per lead, each reply as
received and the document sluice built are the parts of that spend worth keeping, and
`output_dir` is a scratch workspace nothing downstream reads. run.json records
`dry_run: true`, and a dry run replaces an earlier real run's artefacts for the same slug.

An OPT-IN model-judged check (`cv.voice_check`, cv/voice.py) rides the same retry once the
HARD tier is clean, shown the same lines the phrase list reads. Off by default and fails
open on a backend error. At shipped defaults a surviving STYLE finding costs nothing beyond
the retry: `cv.style_hold` (also opt-in) is the ONLY thing that turns it into a sign-off hold
on `tailored_cv`, a separate gate from `cv.require_signoff`, which governs the fabrication
hold alone."""
import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import partial

from sluice.core import status as _status
from sluice.core.backends import BackendError
from sluice.core.candidate import contact_block, full_name
from sluice.core.leads import (FRAMING_KEYS, StalenessPolicy, ambiguous_slug_warnings,
                               framing_entries, index_by_slug)
from sluice.core.layout import asks_for_bullets, build_slots, no_citable_slot
from sluice.core.log import get_logger
from sluice.core.protocols import (CANDIDATE_PROFILE_RELPATH, CV_LAYOUT_RELPATH, EVIDENCE_KINDS,
                                   LayoutError)
from sluice.core.tokens import tool_items
from sluice.core.usage import meter
from sluice.cv import artefacts as _artefacts
from sluice.cv import bundle as _bundle
from sluice.cv import compose as _compose
from sluice.cv.audit import run_audit, unsupported_claims
from sluice.cv.document import assemble, audit_text, model_lines, to_text
from sluice.cv.reply import Reply, extract_json, parse_reply
from sluice.cv.selection import build_pool, named_entries, select, zero_bullet_findings
# The two TIERS separately, never `check_text` (#167). That wrapper scans every line it is
# given for phrases, and the whole point of the split is that the STYLE tier is SCOPED: it
# is handed only the text the MODEL wrote (`cv/document.py::model_lines`). `check_text`
# survives in cv/slop.py for the fixture-cleanliness guards in tests/, and production must
# not reach for it -- an unscoped phrase complaint about an employer, certificate or
# education line is answerable only by renaming the thing it names.
from sluice.cv.slop import check_hard as _slop_hard
from sluice.cv.slop import check_phrases as _slop_phrases
from sluice.cv.terms import unbundled_terms as _unbundled_terms
from sluice.cv.validate import check_selection, entry_facts
from sluice.cv.voice import run_voice

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
    skipped-config (refused before any dossier fetch or compose, `error` naming which note
    refused: #107, the derived candidate name or contact block is blank -- the vault's
    Candidate Profile note is unset or incomplete, and the name becomes the PDF's headline
    and the contact block is emitted verbatim -- or #364/#365/#368, the CV Layout note
    disappeared after the run's prerequisite check),
    dry-run, error (a single lead's exception caught by run_batch -- see run_batch --
    so one bad lead never aborts the rest of the batch),
    backend-unavailable (#333: the backend stayed down after its own retries; run_batch
    stops at the first one rather than spending a retry budget on every remaining lead,
    and `error` carries the reason. The CLI and the MCP tool both report it as a failure)."""
    lead: str
    status: str
    violations: list = field(default_factory=list)
    # `slop`: cv/slop.py's OWN findings, all `SLOP <label>: <snippet>` -- its HARD tier
    # (em dash / "--") plus its phrase stems, the deterministic STYLE tier. On
    # skipped-gate this is the LAST attempt's findings (both tiers, mirroring
    # `violations`); on every other status it is the RETAINED (hard-clean) attempt's phrase
    # findings alone -- its HARD tier is empty by construction, since `best` is only set
    # once `violations` and the HARD slop entries are both empty. Distinct from `audit_flags`, which is the model-judged
    # FABRICATION verdict, from `voice_flags` below, which is the model-judged VOICE
    # verdict, and from `terms` below, cv/terms.py's unbundled-term findings -- separate
    # judges, kept apart rather than merged (#167, Task 16: this field had NO reader
    # from the day it was added, which is the same "computed and discarded" defect #167
    # opened over).
    slop: list = field(default_factory=list)
    audit_flags: list = field(default_factory=list)
    # The model-judged VOICE check's findings (cv/voice.py, opt-in via `cv.voice_check`)
    # for the RETAINED draft -- raw "flag\t<phrase>\t<why>" lines, unprefixed (unlike
    # `slop` above, these are never merged with the deterministic tier: a false
    # "SLOP"-prefixed voice line would misattribute a model judgment to the
    # regex-based linter). Empty whenever voice_check is off, the hard gate never
    # cleared, or the model found nothing -- an empty list here does not by itself
    # prove the reader works; see the populated-case tests instead.
    voice_flags: list = field(default_factory=list)
    # cv/terms.py's unbundled-term findings (#194), `UNBUNDLED TERM '<term>': ...`, for
    # the same draft `slop` describes: the RETAINED one, or on skipped-gate the LAST
    # attempt. Its OWN field rather than riding in `slop` behind a message prefix, for
    # the reason `voice_flags` is: a reader would otherwise parse a string back into a
    # kind. Empty whenever `cv.term_check` is off.
    terms: list = field(default_factory=list)
    skills_dropped: list = field(default_factory=list)     # #365: picks off the pool, over the cap
    bullets_trimmed: list = field(default_factory=list)    # bullets beyond a role's budget
    attribution_check_off: bool = False                    # no verified entry declares Tools:
    served: str | None = None
    backend: str | None = None
    # #18: set when the lead's job description did not arrive, and composition proceeded
    # anyway. TWO producers since #169, not one: `get_or_build()` raising (a blocked or
    # failed fetch, which composes with `jd=""`), and a fetch that succeeded while
    # `jd_arrived` says no (which keeps whatever text it got -- see run_one for why the
    # two arms deliberately differ). This does NOT change control flow (skipping the lead
    # here would be a bigger behaviour change than the SSRF guard should carry), only
    # visibility: without it, "status: rendered" is indistinguishable from a CV genuinely
    # tailored to a real job description.
    dossier_failed: bool = False
    # #165: the Skills Inventory could not be READ (a symlinked corpus, a non-UTF-8 entry)
    # and the CV was composed without its framing section. Visibility, never control flow --
    # the shape `dossier_failed` above established, and it carries the same obligation: a
    # field with no reader is the "computed and discarded" defect #167 opened over. Read by
    # cli.py's per-result line and its summary count, and by mcpserver.py's cv_run.
    #
    # A MISSING corpus is NOT this: `read_evidence` returns [] for one, which is the abstain
    # case and entirely normal. Deliberately NOT stamped onto the exception the way
    # `dossier_failed` is -- that stamp exists so run_batch can report the flag for a lead
    # that RAISED, and the only raise this flag could survive happens after it is already
    # set, at which point the lead is reported `error` and the framing is not the story.
    skills_unreadable: bool = False
    # The run's diagnostic artefacts (cv/artefacts.py: the prompt, each attempt's composed
    # text, run.json) could not all be written, or the previous run's could not all be
    # cleared. Visibility, never control flow -- `dossier_failed`'s shape for the same
    # reason: an artefact exists to diagnose a CV, and failing to keep one must never cost
    # the CV. Set by `run_one` on every way out, including stamped onto the exception for the
    # `error` result a caller builds. Read by cli.py's per-result line and summary count and
    # by mcpserver.py's cv_run. Always False for a lead refused before composition, which
    # writes nothing and so has nothing to fail.
    artefacts_failed: bool = False
    # Why this lead ended without a CV, as a message rather than an exception so a result
    # stays a plain value to print: a backend failure (#333, on `backend-unavailable` and on
    # the single-lead path's `error`), or which note refused a `skipped-config` lead (the
    # Candidate Profile or the CV Layout, #364/#365/#368), so a caller names the right one.
    # Empty otherwise.
    error: str = ""


def _slug(company: str, role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{company}-{role}".lower()).strip("-")[:80] or "lead"


def _jd_keywords(role: str, jd: str) -> list:
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


_IDENTITY_REFUSAL = (
    f"the vault's Candidate Profile note ({CANDIDATE_PROFILE_RELPATH}) has no declared name "
    "or contact details -- fill it in before composing (the name becomes the PDF's "
    "headline, and the contact block is emitted verbatim)")
_LAYOUT_REFUSAL = (
    f"the vault has no CV Layout note ({CV_LAYOUT_RELPATH}) -- create it before composing: "
    "every role heading, date, location and title on the CV comes from it")


def run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False,
            guard_existing_cv=False, policy=StalenessPolicy(), usage=None) -> CvResult:
    """Compose, gate and render one lead's CV and return its `CvResult`. Wraps `_run_one` so
    that every exit, a return or an exception, finishes the run's diagnostic artefacts in one
    place."""
    # The run's diagnostic artefacts (cv/artefacts.py) are FINISHED here, around the real
    # body, rather than beside each of its returns. `_run_one` leaves through several
    # `return CvResult(...)` statements and any exception, and a record written at each of
    # them is one that a return added later can silently skip; wrapping sends every way out
    # through this one place. `record` stays inert until `_run_one` reaches composition, so
    # the early refusals (not shortlisted, held for sign-off, stale, identity or layout
    # unset) write nothing and leave an earlier run's artefacts as they were -- for a held
    # lead, those are what explain the hold.
    record = _artefacts.RunArtefacts(dry_run=dry_run)
    try:
        result = _run_one(note, vault, cvcfg, backend, dossier_cache, renderer=renderer,
                          dry_run=dry_run, guard_existing_cv=guard_existing_cv,
                          policy=policy, usage=usage, record=record)
    except Exception as e:
        # The `error` result is built by the CALLER (run_batch, or `Sluice.compose_cv`'s
        # write-race catch) from the exception alone, so the flag rides on the exception --
        # the channel `dossier_failed` already uses, stamped by `_run_one`'s own handler.
        record.finish_error(e)
        e.artefacts_failed = record.failed
        raise
    record.finish(result)
    result.artefacts_failed = record.failed
    return result


def _run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run,
             guard_existing_cv, policy, usage, record) -> CvResult:
    fm = note.fm
    # The refusals before any spend, cheapest and most specific first. Each one's ORDER is
    # load-bearing:
    #
    # Process ONLY shortlist leads. This enforces the shortlist-only constraint and
    # inherently never touches (never clobbers) application-owned leads.
    if _status.normalize(fm.get("status", "")) != "shortlist":
        return CvResult(note.ref, "skipped-selection")
    # THE LATCH (#60): a lead already held for sign-off (pending_cv set) must NOT be
    # recomposed. run_audit is non-deterministic, so a re-run could re-roll a clean verdict
    # and set tailored_cv without a human ever signing off -- the gate would be a dice
    # reroll, not a hold. Both cv paths route through run_one (single-lead calls it
    # directly; run_batch calls it per lead), so this one early return covers both. `sluice
    # cv signoff [--discard]` is the only way out.
    if fm.get("pending_cv"):
        return CvResult(note.ref, "skipped-needs-signoff")
    # #9: refuse a stale lead before ANY spend -- a dossier fetch drives a real browser and
    # the compose is an LLM call, and tailoring a CV for a closed posting is exactly the
    # spend this exists to stop. AFTER the #60 latch so a held lead still reports
    # skipped-needs-signoff. `blocks`, never `is_stale`: that keeps --include-stale one
    # decision rather than two that could drift apart between here and apply.
    if policy.blocks(fm.get("last_seen", "")):
        return CvResult(note.ref, "skipped-stale")
    # #107: identity comes from the vault's Candidate Profile note, read once, here, before
    # any spend. The name becomes the PDF's headline with no fallback, so a blank one is the
    # "quiet wrong default" bug class this codebase engineers out, applied to the most
    # visible line of an artefact sent under the user's identity. Both halves are checked:
    # #107's report was a fully declared NAME with a blank CONTACT. AFTER the staleness
    # refusal, so a stale lead reports skipped-stale rather than a config complaint that
    # would not have mattered for it.
    candidate = vault.read_candidate_profile()
    cv_name = full_name(candidate)
    if not cv_name.strip() or not contact_block(candidate).strip():
        return CvResult(note.ref, "skipped-config", error=_IDENTITY_REFUSAL)
    # The CV Layout is the structure every CV is assembled into (#364 spec §4.1), so without one
    # there is nothing to compose INTO. `missing_prerequisites` refuses the whole run first;
    # this catches a note deleted since, before any spend. `None` is never read as zero
    # roles. A malformed or unreadable note RAISES here, and run_batch records that lead as
    # `error`: naming the problem is the prerequisite check's job, once per run.
    layout = vault.read_cv_layout()
    if layout is None:
        return CvResult(note.ref, "skipped-config", error=_LAYOUT_REFUSAL)

    company, role = fm.get("company", ""), fm.get("role", "")
    # #329: triage's judgement of this role, as framing for the composer. Formatted ONCE: the
    # same tuple goes to the compose call and to the sign-off snapshot, so a reviewer is
    # shown exactly what the composer was given. The roster of keys is
    # `sluice.core.leads.FRAMING_KEYS`, never hand-typed again here.
    framing = _compose.framing_lines(*(fm.get(key, "") for key in FRAMING_KEYS))
    jd, dossier_failed = "", False
    try:
        d = dossier_cache.get_or_build(fm)
        jd = (d.get("jd") or {}).get("markdown", "")
        # A fetch that SUCCEEDED and produced no JD is the same fact as one that raised
        # (#18), so it earns the same flag: a CV built from the verified bundle alone is
        # degraded rather than fabricated, and the flag is what tells the user which. It is
        # NOT the same control flow, on purpose (#169): this arm KEEPS whatever text the
        # fetch returned, since a short JD costs tailoring QUALITY, not correctness -- the
        # gate checks every bullet against the bundle either way -- while the `except` arm
        # has no text at all. Triage abstains on the same predicate because judging page
        # chrome spends a judge call and writes a verdict nobody can trust; composition has
        # already decided to build a CV.
        if not dossier_cache.jd_arrived(d):
            dossier_failed = True
    except Exception as e:
        _log.warning("dossier for %s failed: %s", note.ref, e)
        dossier_failed = True

    # Everything from here on can raise for reasons unrelated to the dossier (a render
    # failure, a backend timeout mid-compose, a store write conflict), and run_batch's
    # per-lead catch-all then builds the result from the exception alone. `dossier_failed`
    # is a LOCAL, gone once the stack unwinds, so the handler at the bottom stamps it onto
    # the exception -- the one place both are in scope -- and re-raises unchanged.
    try:
        # The experience read is deliberately NOT wrapped: it is the gate's only citable
        # evidence, and a bundle with no ids fails every bullet anyway.
        entries = vault.read_evidence("experience", verified_only=True)
        # Each kind is read ONCE per lead, and every consumer is fed from that read: the
        # bundle's skills framing and the SKILLS pool must come from one revision of a
        # corpus, or one compose could frame one version of the inventory and offer picks
        # from another.
        reads = {"experience": entries}

        def read_once(kind, verified_only=True):
            # TypeError, never ValueError: this is a caller's BUG, and the #165 handler
            # below catches (OSError, ValueError) to degrade an unreadable inventory to a
            # warning -- a ValueError here would be swallowed into that warning too.
            if not verified_only:
                raise TypeError("a CV reads verified evidence only")
            if kind not in reads:
                reads[kind] = vault.read_evidence(kind, verified_only=True)
            return reads[kind]
        # A broken Skills Inventory must not cost a lead (#165): its framing AND its skill
        # names fall back to nothing together. `read_evidence` returns [] for a MISSING
        # directory, so reaching this handler means genuine breakage, which `doctor`
        # already reports per kind; #167's rule is that a thing affecting only tailoring
        # QUALITY may never bin a lead. (OSError, ValueError): a non-UTF-8 note raises
        # UnicodeDecodeError, a ValueError, and catching OSError alone would let it record
        # `error` for EVERY lead in the batch.
        skills_unreadable = False
        try:
            skills = read_once("skills")
            named = named_entries(read_once)
        except (OSError, ValueError) as e:
            _log.warning("skills inventory for %s unreadable, composing without it: %s",
                         note.ref, e)
            skills, named, skills_unreadable = [], [], True
        # Everything below is derived ONCE, before any compose, from the same bundle: a
        # fault knowable here must not cost an LLM call first, and adjacency is what stops a
        # later edit rebuilding one value from a different bundle and leaving another stale.
        b = _bundle.build_bundle(entries, cvcfg.negatives, _jd_keywords(role, jd),
                                 cvcfg.prefix_map, skills=skills)
        slots = build_slots(layout, b["entries"])
        slot_ids = [s.id for s in slots]
        headings = {s.id: s.role.heading for s in slots}
        facts = entry_facts(b, layout)
        # #364 spec §6.6: the misattributed-tool check runs only while some verified entry
        # declares Tools:, and every result says whether it did.
        attribution_off = not any(f.tools for f in facts.values())
        pool = build_pool(named, entries, decoys=cvcfg.fabrication_decoys)
        # The COMPOSER's corpus and the ADVISORY audit's are two functions rather than one
        # with a flag (#364 D11): there is no default for a future caller to get wrong.
        bundle_text = _bundle.render_structured_bundle(b)
        audit_bundle_text = _bundle.render_audit_bundle(b)
        vocab = _bundle.term_vocabulary(b, layout)

        # Composition starts here, and so do the run's diagnostic artefacts. Placed AFTER
        # every step that can fail before a compose: a run that dies there composed nothing,
        # and clearing an earlier run's artefacts on its way to failing would destroy the
        # last diagnosis this lead had in exchange for nothing
        # (tests/test_cv_run_artefacts.py::test_a_run_that_fails_before_composing_leaves_the_last_diagnosis_alone).
        out_dir = f"{cvcfg.output_dir}/{_slug(company, role)}"
        record.begin(out_dir, lead=note.slug, entry_ids=[e["id"] for e in b["entries"]],
                     dossier_failed=dossier_failed, skills_unreadable=skills_unreadable)

        retry_findings = retry_drops = None
        violations, slop_err, slop_msgs, term_msgs, voice_flags = [], [], [], [], []
        selection = None
        # The hard-clean attempt with the FEWEST style/voice findings, as
        # (selection, slop_msgs, term_msgs, voice_flags), or None if no attempt ever was.
        # Retaining it is what lets a STYLE or VOICE finding drive the retry WITHOUT being
        # able to bin a lead (#167): attempt 2 is an unconstrained, non-deterministic
        # compose, so a loop that threw away a hard-clean attempt to chase a phrase would
        # lose the lead whenever the retry came back worse. The findings ride along with the
        # selection they were found IN, because they describe that attempt and no other.
        # Slop and term findings are SEPARATE members (#194) because a `style_hold` hold
        # tags each kind differently, and only the loop knows which is which.
        best, best_voice_measured = None, False
        # Numbered from 1 because the number is user-facing: it names the attempt's
        # artefact files (prompt.attempt-1.txt, reply.attempt-1.txt) and run.json's
        # `retained_attempt`. Two attempts: the retry is bounded at one.
        for attempt in range(1, 3):
            # Only the COMPOSE call is wrapped, never the loop body.
            #
            # ONE backend, THREE stages -- so the stage is attached HERE, per call. `meter`
            # returns `backend` unchanged when `usage` is None (the shipped state, #308).
            # `note.slug`, never `note.ref`: `ref` is an OPAQUE STORE HANDLE (a filesystem
            # path for the vault store), and persisting it would put the vault path in a
            # telemetry file.
            try:
                raw = _compose.compose_structured(
                    meter(usage, backend, "cv-compose", lead=note.slug), bundle_text, jd,
                    company, role, name=cv_name, slots=slots, pool=pool,
                    skills_max=layout.skills_max, prior_findings=retry_findings,
                    prior_drops=retry_drops, slop_allow=cvcfg.slop_allow,
                    triage_framing=framing, on_prompt=partial(record.prompt, attempt))
            except Exception as e:
                # Recorded against its attempt BEFORE either arm below: on the `break` arm
                # the log line is otherwise the only trace that attempt 2 ever ran.
                record.compose_failed(attempt, e)
                # A retry that never RETURNS must not bin a lead attempt 1 already earned.
                # A BackendError -- a timeout, a reply truncated at max_tokens -- would
                # otherwise cross this loop, past the retained attempt, and be recorded as
                # `error`; with `best` set, the only thing that turns an outage into a lost
                # lead is a style finding, and a phrase may never cost a lead (#167). With
                # nothing retained there is no fallback, so a bare `raise` keeps the
                # original traceback and the handler below stamps dossier_failed onto it.
                if best is None:
                    raise
                _log.warning("cv retry compose for %s failed (%s); shipping the retained "
                             "hard-clean draft", note.ref, e)
                break
            # Kept as RECEIVED, before anything reads it: the reply a diagnosis has to start
            # from, whichever way the checks below then rule on it.
            record.composed(attempt, raw)
            # #364 spec §6.0, per attempt: read the reply, select from it, check the selection.
            # A reply that cannot be read has nothing to select: its REPLY findings are the
            # whole of this attempt's verdict.
            obj = extract_json(raw)
            reply = parse_reply(obj, slot_ids, headings) if isinstance(obj, dict) else obj
            voice_flags, voice_failed, voice_measured = [], False, False
            if not isinstance(reply, Reply):
                violations, slop_err, slop_msgs, term_msgs = list(reply), [], [], []
                selection = None
            else:
                selection = select(reply, slots, pool, layout.skills_max)
                # The kept bullets' own text findings first (cv/reply.py keeps a bullet
                # whose text fails a check so that only a KEPT one is refused).
                violations = (list(selection.findings)
                              + zero_bullet_findings(selection, slots)
                              + check_selection(selection, slots, facts,
                                                decoys=cvcfg.fabrication_decoys))
                lines = model_lines(selection, slots)
                # The BLOCKING slop tier -- an em dash or a literal `--` -- over the model's
                # texts only (#364 spec §6.1): a vault string carrying one is the user's and
                # renders. Reported in `slop`, beside the phrase tier, as it always has been.
                slop_err = [f"SLOP {label}: {snip}" for _n, text in lines
                            for _ln, label, snip in _slop_hard(text)]
                # The STYLE tier over the text the MODEL wrote and nothing else (#364
                # spec §6.3): a slop stem inside a certificate or a skill's name is the user's,
                # and a complaint about it is answerable only by renaming the thing it names.
                slop_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip
                             in _slop_phrases(lines, allow=cvcfg.slop_allow)]
                # The unbundled-term check (#194) over the same lines, in its OWN list so a
                # `style_hold` hold can tag it `term\t` from what the loop already knows.
                term_msgs = ([f"UNBUNDLED TERM {term!r}: named nowhere in your evidence: "
                              f"{snip}" for _ln, term, snip in _unbundled_terms(lines, vocab)]
                             if cvcfg.term_check else [])
                # The SAME lines the deterministic half of the STYLE tier read, joined for the
                # model-judged half: the scoping is a property of the TIER, so the voice
                # judge may never drive a retry on a line the phrase list cannot see.
                excerpt = "\n".join(text for _ln, text in lines)
                if not violations and not slop_err:
                    # The model-judged VOICE check (#167, cv/voice.py). Gated three ways:
                    # `voice_check` (opt-in, so an unconfigured install spends no extra LLM
                    # call), a hard-clean attempt (no point judging the voice of one about
                    # to be recomposed anyway), and non-blank prose (a finding against an
                    # empty excerpt could name nothing yet would cost the retry). Fails
                    # OPEN, as the audit does: a backend error must never make this gate
                    # HARDER than the check that actually ran, nor turn a style-clean,
                    # voice-untested attempt into a lost lead.
                    if cvcfg.voice_check and excerpt.strip():
                        try:
                            _report, voice_flags = run_voice(
                                meter(usage, backend, "cv-voice", lead=note.slug), excerpt)
                            voice_measured = True
                        except Exception as e:
                            _log.warning("voice check for %s failed (%s); treating as "
                                         "clean", note.ref, e)
                            voice_flags, voice_failed = [], True
                    # Keep the hard-clean attempt with FEWER style/voice findings, not merely
                    # the LAST one (#194). A TIE keeps the later attempt: it was composed with
                    # the earlier findings in front of it, so only a strictly worse retry is
                    # refused. A voice check that RAISED failed open to no flags, which
                    # counts as zero findings although nothing was judged, so an unmeasured
                    # attempt never displaces a MEASURED one -- otherwise a style-worse
                    # attempt could replace a cleaner one behind an outage. The guard is
                    # deliberately ONE-WAY: a measured retry facing an unmeasured retained
                    # attempt is judged on the count, the one fact both genuinely have.
                    found = len(slop_msgs) + len(term_msgs) + len(voice_flags)
                    if best is None or (
                            found <= len(best[1]) + len(best[2]) + len(best[3])
                            and not (voice_failed and best_voice_measured)):
                        best = (selection, slop_msgs, term_msgs, voice_flags)
                        best_voice_measured = voice_measured
                        # Inside the same condition, so run.json's `retained_attempt` names
                        # the attempt actually kept rather than the last one examined.
                        record.retained(attempt)
                    if not found:
                        break
            # ALL THREE tiers reach the composer: a style or voice finding is worth one
            # retry -- it is the whole of #167's complaint that these were computed and
            # thrown away. The VOICE prefix mirrors the SLOP one: the same model reads both,
            # in the same retry prompt.
            retry_findings = (violations + slop_err + slop_msgs + term_msgs
                              + [f"VOICE: {flag}" for flag in voice_flags])
            # #364 spec §6.2: a drop never causes a retry, but a retry lists them so the model
            # can choose better.
            retry_drops = (list(selection.skills_dropped + selection.bullets_trimmed)
                           if selection is not None else None)

        backend_used = getattr(backend, "label", None)
        if best is None:
            # No attempt was ever hard-clean: every finding describes the LAST attempt,
            # since no attempt was ever worth retaining. The lead is skipped -- never
            # rendered ungated.
            return CvResult(
                note.ref, "skipped-gate", violations=violations, slop=slop_err + slop_msgs,
                terms=term_msgs, voice_flags=voice_flags, backend=backend_used,
                dossier_failed=dossier_failed, skills_unreadable=skills_unreadable,
                skills_dropped=list(selection.skills_dropped) if selection else [],
                bullets_trimmed=list(selection.bullets_trimmed) if selection else [],
                attribution_check_off=attribution_off)
        # THE REBIND, before ANYTHING below reads the selection. The loop's names are bound
        # to the LAST attempt, which on [attempt 1 hard-clean, attempt 2 hard-dirty] is one
        # that never cleared the gate. Everything past this line -- the assembly, the audit
        # whose flags drive the sign-off hold, the render -- must read the RETAINED attempt,
        # or the engine renders one attempt while auditing another and a fabricated claim in
        # the served CV goes un-held. ONE assignment covers every reader because they all
        # read these names; keep it that way rather than passing `best[0]` at a call site,
        # which is what would let a reader added later quietly miss it. The findings are
        # rebound with it: each describes the retained attempt and no other.
        selection, slop_msgs, term_msgs, voice_flags = best
        report = dict(slop=slop_msgs, terms=term_msgs, voice_flags=voice_flags,
                      backend=backend_used, dossier_failed=dossier_failed,
                      skills_unreadable=skills_unreadable,
                      skills_dropped=list(selection.skills_dropped),
                      bullets_trimmed=list(selection.bullets_trimmed),
                      attribution_check_off=attribution_off)

        assembled = assemble(layout, slots, selection, candidate)
        # Kept BEFORE the audit, so a dry run, and a run whose audit or render raises, still
        # leave the document sluice built -- each bullet with its citations -- beside the
        # replies it came from (#364 spec §7.4).
        record.rendering(to_text(assembled.document, cites=assembled.cites))
        # The audit reads what the MODEL wrote, never vault text (#364 spec §6.4). It is advisory
        # to the MODEL (cv/audit.py: "NEVER blocks"), so a failure here never stops a CV that
        # cleared the HARD gate from rendering. But whether it RAN is not advisory (#333): an
        # audit that could not run has checked nothing, so under `cv.require_signoff` the CV
        # is HELD below exactly as an `unsupported` flag would hold it, instead of being
        # written send-ready as if it had passed.
        audit_unavailable = ""
        try:
            _report, audit_flags = run_audit(
                meter(usage, backend, "cv-audit", lead=note.slug),
                audit_text(selection, slots), audit_bundle_text)
        except Exception as e:
            _log.warning("advisory audit failed for %s: %s", note.ref, e)
            audit_flags, audit_unavailable = [], str(e)
        if dry_run:
            return CvResult(note.ref, "dry-run", audit_flags=audit_flags, **report)

        from sluice.cv import render as _render
        # The renderer is INJECTED, never built here: an engine that constructs its own
        # adapter breaks both the seam and the offline tests. It is reached only past the
        # HARD gate above, so a renderer -- which validates nothing -- is never handed a CV
        # with outstanding findings. `out_dir` was bound where the artefacts began, so the
        # PDF and the artefacts that explain it share one directory by construction.
        pdf = renderer.render(assembled.document, out_dir,
                              neutral_name=cvcfg.neutral_filename)
        record.rendered(pdf)
        served = (_render.serve(pdf, cvcfg.served_dir, served_prefix=cvcfg.served_prefix)
                  if cvcfg.served_dir else None)
        # An `unsupported` audit flag WITHHOLDS the send-ready pointer until a human signs off
        # (#60); only `unsupported` (never `paraphrase`, which is legitimate tailoring)
        # blocks. An audit that could not run holds too (#333), as an `unaudited\t<reason>`
        # entry. A surviving STYLE finding earns the SAME consequence under `cv.style_hold`
        # (#167) -- deliberately a SEPARATE gate from `require_signoff`, whose True default
        # was chosen for FABRICATION: riding it would withhold tailored_cv on ~40 stems out
        # of the box. The findings describe the RETAINED attempt (the rebind above), so the
        # hold never fires on a worse retry.
        #
        # Each finding is its own entry in the SAME flat claims array, tagged by kind
        # (`style\t`, `term\t` -- #194: a probable INVENTION, which the sign-off prompt must
        # not call a style concern), because the Store protocol's `claims` stays a plain
        # JSON ARRAY and a wrapped object would collapse into one bogus claim string.
        # `sluice/cli.py::_print_signoff_claims` is the roster of the tags. #329's
        # `framing\t` entries -- the triage notes the composer was given, from the same
        # `framing` tuple -- are appended AFTER the blockers and never cause a hold.
        style_blockers = ([f"style\t{msg}" for msg in slop_msgs + voice_flags]
                          + [f"term\t{msg}" for msg in term_msgs]
                          if cvcfg.style_hold else [])
        unaudited = [f"unaudited\t{audit_unavailable}"] if audit_unavailable else []
        blockers = (
            (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
            + style_blockers)
        if served and blockers:
            # Record what to promote (pending_cv) and what to review (needs_signoff); withhold
            # tailored_cv. The served PDF stays in served_dir (it passed the HARD gate) but is
            # inert without the pointer. hold_for_signoff stamps ONLY IF no tailored_cv exists
            # yet (checked on fresh content): a CV that appeared during compose must not latch
            # the lead behind a redundant hold -- it reports skipped-has-cv instead.
            held = vault.hold_for_signoff(
                note.ref, pending=f"{served} ({date.today().isoformat()})",
                claims=json.dumps(blockers + framing_entries(framing)))
            if not held:
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags, **report)
            return CvResult(note.ref, "needs-signoff", audit_flags=audit_flags,
                            served=served, **report)
        if served:
            wrote = vault.set_tailored_cv(
                note.ref, f"{served} ({date.today().isoformat()})",
                only_if_absent=guard_existing_cv)
            if guard_existing_cv and not wrote:
                # A CV appeared for this lead during our compose+render window; do not
                # clobber it (#16). The served PDF stays in served_dir (it passed the gate);
                # only the note pointer is withheld.
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags, **report)
        return CvResult(note.ref, "rendered", audit_flags=audit_flags, served=served,
                        **report)
    except Exception as e:
        e.dossier_failed = dossier_failed
        raise


def missing_prerequisites(vault) -> list:
    """Config-level preconditions EVERY lead in a run shares, as user-facing strings (#242),
    checked ONCE per run before any spend -- and deliberately not in `run_one`, because a
    fact equally true of every lead belongs in one line, not N.

    #364 spec §9.1. The CV Layout note: absent, malformed or unreadable, each said its own way
    since the remedies differ. A verified experience entry whose `Tools:` the gate cannot
    use, naming the entry and the item on the user's own terminal (doctor's row counts them
    instead, since its rows reach MCP clients). No slot that can cite anything while the
    layout asks for bullets -- typically companies the headings do not match. And an empty or
    unreadable citable corpus. UNREADABLE is never reported as ABSENT: a read failure carries
    its own error, the rule `Vault.preflight` follows for the same corpora.

    The corpus check is keyed on `cited_by_gate`, not on every kind: an empty Skills
    Inventory or STAR Stories corpus cannot make a CV fail, so neither is required. And an
    EMPTY citable corpus is refused only while some role asks for bullets: a layout whose
    every role has `bullets_max: 0` is a headings-only CV the user configured (#364 spec §5.2,
    #364 D10), which cites nothing, so it needs no entry. A layout that is absent or did not
    parse is already refused above, and the empty corpus is reported beside it."""
    missing = []
    layout = None
    try:
        layout = vault.read_cv_layout()
    except LayoutError as exc:
        missing.append(f"your CV Layout note ({CV_LAYOUT_RELPATH}) is malformed:\n      - "
                       + "\n      - ".join(exc.problems))
    except (OSError, ValueError) as exc:         # ValueError covers UnicodeDecodeError
        missing.append(f"cannot read your CV Layout note ({CV_LAYOUT_RELPATH}) -- {exc}")
    else:
        if layout is None:
            missing.append(f"no CV Layout note at {CV_LAYOUT_RELPATH} -- every role heading, "
                           "date, location and title on a CV comes from it "
                           "(docs/CONFIGURATION.md shows its shape)")
    for name, kind in EVIDENCE_KINDS.items():
        if not kind.cited_by_gate:
            continue
        try:
            entries = vault.read_evidence(name)
        except (OSError, ValueError) as exc:
            # Report WHY, and never name `<kind> add`: it reaches the corpus through the
            # same resolver, so it would fail identically.
            missing.append(f"cannot read your {name} entries -- {exc}")
            continue
        if not entries:
            if layout is not None and not asks_for_bullets(layout.roles):
                continue
            missing.append(
                f"no verified {name} entries -- every WORK bullet must cite one, so the "
                f"fabrication gate would reject any CV composed without them. "
                f"Add with `job-sluice {name} add`, then `job-sluice {name} verify`")
            continue
        for entry in entries:
            try:
                tool_items(entry)
            except ValueError as exc:
                missing.append(f"your {name} entry {entry['title']!r}: {exc}")
        if layout is not None:
            # The ids are only labels here: build_slots keys eligibility by them.
            slots = build_slots(layout, [dict(e, id=str(i)) for i, e in enumerate(entries)])
            if no_citable_slot(slots):
                missing.append(
                    f"no role in your CV Layout can cite any verified {name} entry -- each "
                    "entry's Company: must equal one of a role's employers (its heading when "
                    "it lists none), or be listed under any_role; `job-sluice doctor` counts the entries "
                    "that match nothing ('not on your CV', 'no company')")
    return missing


def run_batch(vault, cvcfg, backend, dossier_cache, *, renderer, limit=None,
              dry_run=False, policy=StalenessPolicy(), usage=None) -> list:
    notes = [n for n in vault.read_leads({"shortlist"})]
    # A consumer of a `read_leads` list that walked it without the slug guard (#1) --
    # not claimed as the LAST: #109's triage/engine.py reached the identical defect by a
    # different route (keyed on a dossier cache hash, not a bare walk) and needed the same
    # fix, which is why this comment no longer counts consumers. `index_by_slug` is the
    # shared verdict -- track, `leads expire`, `apply`'s batch path, and triage's enrich
    # pass all take the same one -- so the call sites cannot drift into different opinions
    # about what ambiguous means. Only the second element is wanted: this pass walks notes,
    # not slugs, which is exactly the shape that let `apply/select.py:select_all` keep both
    # twins.
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
                                   guard_existing_cv=True, policy=policy, usage=usage))
        except Exception as e:
            if isinstance(e, BackendError) and e.transient:
                # A TRANSIENT backend error reaching here has outlived the backend's own
                # retries: the backend is down (#333). Every remaining lead would spend a
                # full retry budget failing the same way, so the batch stops and says so. A
                # NON-transient one (a truncation, a 400) is a property of this lead's
                # prompt, and stays a per-lead `error` below like any other exception.
                _log.warning("cv: backend unavailable at %s, stopping the batch: %s",
                             note.ref, e)
                results.append(CvResult(note.ref, "backend-unavailable", error=str(e),
                                        dossier_failed=getattr(e, "dossier_failed", False),
                                        artefacts_failed=getattr(e, "artefacts_failed",
                                                                 False)))
                break
            _log.warning("cv run failed for %s: %s", note.ref, e)
            # run_one stamps dossier_failed onto the exception before re-raising (see
            # its own comment) precisely so this catch-all -- which must stay a
            # catch-all, for the isolation reason above -- does not silently under-
            # report "N CV(s) composed blind" (cli.py's summary line) for a lead whose
            # dossier WAS blocked but which then also failed downstream for an
            # unrelated reason. `getattr(..., False)` also covers an exception raised
            # by code that predates #18 and so never carries the attribute.
            # `artefacts_failed` crosses the same way, stamped by run_one's wrapper.
            results.append(CvResult(note.ref, "error",
                                    dossier_failed=getattr(e, "dossier_failed", False),
                                    artefacts_failed=getattr(e, "artefacts_failed", False)))
        # needs-signoff counts toward --limit alongside rendered/dry-run: a held lead did
        # the full (expensive) compose + render + serve; only the pointer was withheld, so
        # it consumed a unit of the requested work just as a rendered one did.
        if limit and sum(1 for r in results
                         if r.status in ("rendered", "dry-run", "needs-signoff")) >= limit:
            break
    return results
