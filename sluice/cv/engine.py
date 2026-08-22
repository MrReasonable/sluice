# sluice/cv/engine.py
"""CV tailoring orchestrator: select -> bundle -> compose -> gate -> render -> serve
-> record -> notify. Composition is a bounded backend call over the closed verified
bundle. The gate has two tiers: a HARD one (fabrication, structure, the renderer's own
precheck, em dashes) and a SCOPED STYLE one (#167: AI-slop phrases, in PROFILE prose and
WORK bullets only). Either triggers exactly one retry with the findings fed back, and the
loop RETAINS the last HARD-clean draft -- so the lead is skipped (never rendered ungated)
when no attempt ever cleared the hard tier, and never merely over a phrase. dry_run
computes and reports but writes nothing.

An OPT-IN third signal (`cv.voice_check`, cv/voice.py) rides the same retry once the HARD
tier is clean: a model judgment of the draft's VOICE, for the AI-tell phrasing a fixed
phrase list cannot catch. It is the SECOND HALF of the STYLE tier and is scoped the same
way -- the model is shown the same PROFILE/WORK lines the phrase list is handed, never
the whole document. Off by default and fails open on a backend error -- see the comment
at its call site below.

At shipped defaults a STYLE finding that survives the retry costs nothing beyond that
retry: `cv.style_hold` (also opt-in, off by default) is the ONLY thing that turns it into
a #60-style sign-off hold on `tailored_cv` -- see the comment at that call site for why it
is a separate gate from `cv.require_signoff`, which continues to gate the fabrication hold
alone."""
import json
import re
from dataclasses import dataclass, field
from datetime import date

from sluice.core import status as _status
from sluice.core.candidate import contact_block, full_name
from sluice.core.leads import StalenessPolicy, ambiguous_slug_warnings, index_by_slug
from sluice.core.log import get_logger
from sluice.cv import bundle as _bundle
from sluice.cv import compose as _compose
from sluice.cv.audit import run_audit, unsupported_claims
# The two TIERS separately, never `check_text` (#167). That wrapper scans every line of
# the document for phrases, and the whole point of the split is that the STYLE tier is
# SCOPED: it is handed only the PROFILE prose and WORK bullets `section_spans` yields.
# `check_text` survives in cv/slop.py for the fixture-cleanliness guards in tests/, and
# production must not reach for it -- an unscoped phrase complaint about an employer,
# certificate or education line is answerable only by renaming the thing it names.
from sluice.cv.slop import check_hard as _slop_hard
from sluice.cv.slop import check_phrases as _slop_phrases
from sluice.cv.validate import section_spans, validate as _validate
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
    skipped-config (#107: the derived candidate name or contact block is blank --
    the vault's Candidate Profile note is unset or incomplete -- refused before any
    dossier fetch or compose -- see run_one; the name becomes the PDF's <h1> and the
    contact block is emitted verbatim, and a composer complying with the prompt as
    given produces a header no STRUCTURAL guard can distinguish from a genuine one),
    dry-run, error (a single lead's exception caught by run_batch -- see run_batch --
    so one bad lead never aborts the rest of the batch)."""
    lead: str
    status: str
    violations: list = field(default_factory=list)
    # `slop`: the deterministic slop-linter's OWN findings (cv/slop.py's HARD tier --
    # em dash / "--" -- plus the scoped STYLE tier of AI-tell phrases), each already
    # formatted "SLOP <label>: <snippet>" so a reader never has to know which tier
    # produced it. On skipped-gate this is the LAST attempt's findings (both tiers,
    # mirroring `violations`/`slop_err` in that branch's own comment); on every other
    # status it is the RETAINED (hard-clean) draft's STYLE tier alone -- its HARD tier
    # is empty by construction, since `best` is only set once `hard_msgs` (which
    # includes the HARD slop entries) is empty. Distinct from `audit_flags`, which is
    # the model-judged FABRICATION verdict, and from `voice_flags` below, which is the
    # model-judged VOICE verdict -- three different judges, kept apart rather than
    # merged (#167, Task 16: this field had NO reader from the day it was added,
    # which is the same "computed and discarded" defect #167 opened over).
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


def _slug(company: str, role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{company}-{role}".lower()).strip("-")[:80] or "lead"


def _jd_keywords(role: str, jd: str) -> list:
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


# The two axes along which a composer can RE-RENDER the declared contact block without
# replacing it. Ordered most-specific-first purely so the message below reads naturally;
# the membership test itself is order-independent.
_CONTACT_REWORDINGS = (
    ("CASE", lambda ln: ln.casefold()),
    ("SPACING", lambda ln: " ".join(ln.split())),
)


def _contact_key(lines, axes):
    """`lines` with every named axis normalised away."""
    out = []
    for ln in lines:
        for name, fn in _CONTACT_REWORDINGS:
            if name in axes:
                ln = fn(ln)
        out.append(ln)
    return out


def _same_contact_reworded(found, expected) -> bool:
    """Is `found` the declared contact block, differing only in how it was RENDERED?

    True means the composer kept every contact line's content and changed only its case,
    its internal spacing, or both. That is a different failure from a preamble having
    REPLACED a line, and it needs a different instruction back to the model -- the retry
    gets exactly one attempt, and "drop the preamble" is unactionable when there is none.

    It does NOT make the engine accept the difference. The refusal is identical either
    way, because `cv/parse.py` reads the contact from the composed TEXT and the engine
    never substitutes `cv_contact` back in, so whatever clears the caller's check is what
    renders. Only the diagnosis changes.
    """
    all_axes = {name for name, _ in _CONTACT_REWORDINGS}
    return _contact_key(found, all_axes) == _contact_key(expected, all_axes)


def _contact_rewording(found, expected) -> str:
    """Which axis (or axes) `found` differs from `expected` on, for the message.

    Derived by testing each axis alone rather than hand-branched, so the combined case
    ("CASE and SPACING") cannot be the one spelling somebody forgets to write.

    The `"RENDERING"` fallback is UNREACHABLE from the sole caller, and is here only so a
    future caller that skips the `_same_contact_reworded` precondition gets a sane word
    rather than the malformed sentence an empty join would produce ("the contact block's
    was changed"). Unreachable because the axes are independent normalisations: if two
    lists are equal under both but differ raw, then dropping one axis must expose the
    other, so `differs` cannot come back empty. Brute-forced over a small alphabet
    (whitespace runs, tabs, case variants, blanks; 528 qualifying pairs) rather than
    argued -- zero reached it. Stated because an unexplained unreachable branch reads as
    either dead code or an unproven claim, and it is neither.
    """
    all_axes = {name for name, _ in _CONTACT_REWORDINGS}
    differs = [name for name, _ in _CONTACT_REWORDINGS
               if _contact_key(found, all_axes - {name}) != _contact_key(expected, all_axes - {name})]
    return " and ".join(differs) if differs else "RENDERING"


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

    # #107: identity now comes from the vault's Candidate Profile note, not
    # cv.name/cv.contact -- read once, here, before any spend. This SUPERSEDES the
    # old #99 sentinel comparison (cvcfg.name.strip() == "Your Name") and is
    # strictly simpler: a blank derived name or contact block just IS blank, so no
    # placeholder trick is needed to tell a configured value from an unconfigured
    # one -- "" cannot collide with a real name the way "Your Name" theoretically
    # could. It is also the direct fix for #107's real report: a NAME could be
    # fully declared while the CONTACT stayed blank, and the old check -- keyed
    # only on cvcfg.name -- let that lead all the way to compose, paying a dossier
    # fetch and an LLM call, only to fail the STRUCTURAL header guard below on
    # every attempt, forever. Checking both derived values here catches that shape
    # before either spend, not after.
    #
    # Every composed CV's header block is required to end with cv_name as the
    # exact candidate name line and begin with cv_contact's lines (see the
    # STRUCTURAL guard below), and compose.py's prompt shows the model whatever
    # these resolve to -- a composer complying with the prompt as given produces a
    # header no STRUCTURAL guard can distinguish from a genuine one. `cv_name`
    # becomes the PDF's <h1> (sluice/templates/cv_plain.html.j2) with no length cap
    # and no fallback, so a blank one is the "quiet wrong default" bug class this
    # codebase most consistently engineers out, applied to the most visible line of
    # an artefact sent under the user's identity. Refused BEFORE any spend -- a
    # dossier fetch drives a real browser and compose is an LLM call -- mirroring
    # the #9 staleness guard immediately above, which this sits after so a stale
    # lead still reports skipped-stale rather than a config complaint that would
    # not have mattered for it anyway.
    profile = vault.read_candidate_profile()
    cv_name = full_name(profile)
    cv_contact = contact_block(profile)
    if not cv_name.strip() or not cv_contact.strip():
        return CvResult(note.ref, "skipped-config")

    company, role = fm.get("company", ""), fm.get("role", "")
    jd, dossier_failed = "", False
    try:
        d = dossier_cache.get_or_build(fm)
        jd = (d.get("jd") or {}).get("markdown", "")
        # A fetch that SUCCEEDED and produced no JD is the same fact as one that raised
        # (#18), so it earns the same flag: a CV built from the verified bundle alone is
        # degraded rather than fabricated, and the flag is what tells the user which.
        #
        # It is NOT the same control flow, and the difference is deliberate. The `except`
        # arm below has no text at all, so it composes with `jd=""`. This arm does have
        # text -- `jd` was bound from the fetched dossier above, BEFORE this test -- and
        # KEEPS it. At the shipped `min_jd_chars: 0` the two are identical, because only a
        # wholly empty JD fails the predicate. Above the floor they differ: a sub-floor JD
        # is still handed to compose().
        #
        # That is on purpose, and it is why this does not mirror triage. Triage abstains
        # on the same predicate because judging page chrome spends a real judge call and
        # writes a verdict nobody can trust. Composition has already decided to build a
        # CV; a short JD costs tailoring QUALITY, not correctness, and it is not a
        # fabrication risk -- the gate still citation-checks every bullet against the
        # bundle either way. Throwing away text the fetch actually returned would make the
        # artefact worse for no safety gain.
        if not dossier_cache.jd_arrived(d):
            dossier_failed = True
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

        retry_msgs, cv_text, violations, slop_err = None, "", [], []
        # The last attempt that cleared the HARD gate, as `(cv_text, style_msgs,
        # voice_flags)`, or None if no attempt ever did. Retaining it is what lets a
        # STYLE or VOICE finding drive the retry WITHOUT being able to bin a lead
        # (#167): attempt 2 is an unconstrained, non-deterministic compose, so a loop
        # that threw away a hard-clean draft to chase a phrase would lose the lead
        # whenever the retry came back worse -- a CV that renders today. The findings
        # ride along with the draft they were found IN, because they describe that text
        # and no other; re-deriving them later from whatever `cv_text` happens to hold
        # is the mistake the rebind below exists to prevent.
        #
        # `voice_flags` is a THIRD tuple element, not folded into `style_msgs` with a
        # distinguishing prefix (Task 16 needs the two apart as `CvResult.voice_flags`
        # vs. the deterministic slop findings, the same way `audit_flags` already means
        # fabrication and nothing else -- prefixing would make Task 16 parse a string
        # back into a verdict, which is the fragile direction).
        best = None
        for _ in range(2):
            # Only the COMPOSE call is wrapped, never the loop body. The body raises a
            # deliberate TypeError when a renderer breaks the `precheck` contract below,
            # and that must keep propagating: `precheck` runs on attempt 1 too, so a
            # broken renderer raises before any draft is retained and this arm would
            # never see it -- but a wrapper around the body would silently convert it
            # into "ship whatever attempt 1 produced" the moment the retry re-raised it.
            try:
                # slop_allow=cvcfg.slop_allow (#167, Task 17): without this, the
                # config knob would still suppress the STYLE HOLD (via _slop_phrases'
                # own `allow` below) while the PROMPT kept instructing the model
                # against the very phrase the candidate asked to keep -- suppressing
                # the hold while composing the candidate's own voice out anyway.
                cv_text = _compose.compose(backend, bundle_text, jd, company, role,
                                           name=cv_name, contact=cv_contact,
                                           employers=cvcfg.employers,
                                           prior_violations=retry_msgs,
                                           slop_allow=cvcfg.slop_allow)
            except Exception as e:
                # A retry that never RETURNS must not bin a lead attempt 1 already
                # earned. compose() catches nothing, so a BackendError -- a timeout,
                # every fallback leg down, a reply truncated at max_tokens -- would
                # otherwise cross this loop, past the retained draft, and be recorded as
                # `error`. Measured: that same failure is HARMLESS when attempt 1 is
                # style-clean, because the loop has already broken and no second compose
                # happens. So with `best` set, the ONLY thing that turns a backend outage
                # into a lost lead is a phrase match, and a phrase may never cost a lead
                # (#167).
                #
                # With nothing retained there is no fallback to prefer, so a bare `raise`
                # keeps today's behaviour AND the original traceback -- and run_one's
                # outer handler still stamps dossier_failed onto it on the way out.
                if best is None:
                    raise
                # Never silently: a swallowed backend failure that logs nothing is a lead
                # composed once instead of twice with no trace of why.
                _log.warning("cv retry compose for %s failed (%s); shipping the retained "
                             "hard-clean draft", note.ref, e)
                break
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
            # STRUCTURAL guards #3/#4 (#99): the header block before PROFILE must have
            # exactly the shape compose.py's own prompt requested -- cv_contact's
            # lines, then the name heading, and nothing else. The comment beside
            # `parse_cv`'s `header_lines[-1]` assignment (cv/parse.py) already
            # explains why no SHAPE test can tell a genuine name/contact line from a
            # composer's stray preamble sentence in isolation ("a name can look
            # like anything, contact details are not universally regex-shaped"); these
            # guards do not try to. They compare against `cv_name`/`cv_contact` (#107:
            # derived from the vault's Candidate Profile note, once at the top of this
            # function), ground truth the parser never has (parse.py is pure and takes
            # only `text`). Recomputed here rather than reached through cv.parse
            # (test_the_engine_no_longer_imports_the_template_grammar forbids the
            # import): the engine may guard what the PROMPT required of every
            # renderer alike; only a renderer may guard what its own LAYOUT needs,
            # and `script` implements no `precheck` at all to reach.
            #
            # Three checks, chained so each only evaluates when the ones before it found
            # nothing wrong (an earlier violation already explains itself; a later
            # complaint on top of it would just restate the same underlying defect in a
            # second sentence). A count mismatch alone misses a same-count REORDERING
            # (name emitted before contact, the opposite of what compose.py requests);
            # an anchor mismatch alone misses "preamble + otherwise-correct name", where
            # the preamble becomes the printed CONTACT block and the name line itself is
            # never inspected. Measured on the real production path (#99): a preamble
            # prefixed onto an otherwise flawless CV parsed with a LinkedIn-URL line as
            # the candidate's NAME and the real name buried in CONTACT -- validate()
            # alone reported zero violations.
            #
            # Neither the count nor the anchor check alone catches a same-count
            # SUBSTITUTION: a preamble sentence occupying exactly the contact slot,
            # with the real name still correctly anchored as the last line. That
            # passes both of the checks above while silently dropping the real contact
            # information (CodeRabbit, PR #100 review, on this same #99 guard) -- so the
            # third check compares header[:-1] against cv_contact's own non-empty
            # lines, not merely their count. It runs LAST, after the anchor check,
            # because a same-count REORDERING (REVERSED_HEADER_CV) also fails this
            # comparison and the anchor check's message ("not the name heading") is the
            # more specific diagnosis for that shape; this check exists for the
            # shape neither of the other two names.
            header_lines = cv_text.splitlines()
            profile_idx = next((i for i, ln in enumerate(header_lines)
                                if ln.strip().upper() == "PROFILE"), None)
            if profile_idx is not None:
                header = [ln.strip() for ln in header_lines[:profile_idx] if ln.strip()]
                expected_contact = [ln.strip() for ln in cv_contact.splitlines() if ln.strip()]
                expected_n = len(expected_contact) + 1
                if len(header) != expected_n:
                    violations = [f"STRUCTURAL: expected {expected_n} line(s) before "
                                  f"PROFILE (the declared contact block, then the "
                                  f"name heading) but found {len(header)} -- drop any "
                                  f"extra text (a preamble, acknowledgement, or "
                                  f"separator) before the contact block"] + violations
                elif header and header[-1].casefold() != cv_name.strip().casefold():
                    violations = [f"STRUCTURAL: the line immediately before PROFILE is "
                                  f"{header[-1]!r}, not the name heading "
                                  f"{cv_name.upper()!r} -- the parser takes the LAST "
                                  f"line before PROFILE as the candidate's "
                                  f"name"] + violations
                elif header[:-1] != expected_contact:
                    # Two different inputs reach here, and they need different remedies.
                    # A CASE-ONLY difference is a composer re-casing a contact line, not
                    # a preamble displacing one, and the retry gets exactly one attempt
                    # off this message -- telling it to "drop the preamble" when there is
                    # no preamble bins a gate-clean CV on a wrong diagnosis (CodeRabbit,
                    # PR #161).
                    #
                    # The REFUSAL is the same either way, deliberately: unlike the name
                    # anchor above -- which case-folds because a CV name heading is
                    # conventionally uppercase, so case drift there is what compose.py's
                    # prompt ASKED for, and the message prints `cv_name.upper()` saying
                    # so -- the prompt asks for the contact block VERBATIM. And the
                    # engine never substitutes `cv_contact` back in: `cv/parse.py` takes
                    # the contact from `header_lines[:-1]`, the composed TEXT, so
                    # whatever clears this check is what renders. Case-folding the
                    # comparison would therefore let a re-cased LinkedIn URL, postcode or
                    # name print on the PDF under the candidate's own identity. The
                    # engine may guard what the prompt required; it required this exactly.
                    # Keyed on a NORMALISATION rather than on an enumerated list of
                    # shapes: case was the first one found, internal whitespace the
                    # second (CodeRabbit, PR #161, twice), and enumerating arms one
                    # review round at a time is how the `template` parser's refusal list
                    # grew. `_same_contact_reworded` answers "is this the declared
                    # contact, rendered differently?" once, so a third rendering variant
                    # lands in the right arm without another arm being added.
                    #
                    # Whitespace has to be in that normalisation even though `header` and
                    # `expected_contact` are already per-line stripped: an INTERNAL run
                    # survives the strip, and `full_name` collapses runs on the name side
                    # while `contact_block` deliberately does not -- so a composer that
                    # renders `+1 555  0100` as `+1 555 0100` is neither equal nor
                    # case-equal, and without this lands on the preamble message.
                    if _same_contact_reworded(header[:-1], expected_contact):
                        violations = [f"STRUCTURAL: the contact block's "
                                      f"{_contact_rewording(header[:-1], expected_contact)} "
                                      f"was changed -- reproduce the declared contact "
                                      f"lines exactly as given, character for character "
                                      f"(they are emitted verbatim into the rendered "
                                      f"CV)"] + violations
                    else:
                        violations = ["STRUCTURAL: the lines before the name heading do not "
                                      "match the declared contact block -- a preamble or "
                                      "other text has replaced a real contact line"] + violations
            # Ask the RENDERER, inside the retry loop, in the same shape a gate violation
            # takes. cv/validate.py never checks the `template` renderer's meta-line
            # grammar (`MM/YYYY-MM/YYYY | LOCATION | Role`) -- only the citation gate does
            # -- so a CV that clears every fabrication check can still be unrenderable by
            # it. Discovering that at render time would be AFTER the LLM spend with no
            # recovery: this loop is the only retry there is, and it closes before render
            # ever runs. Asking here means the model gets one chance to fix its own
            # formatting, feeding retry_msgs into the second compose prompt exactly like a
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
                reported = _precheck(cv_text)
                # The seam's contract is `precheck(cv_text) -> list[str]`
                # (core/protocols.py), and NOTHING types it: `precheck` is deliberately
                # not a Protocol member, so a renderer returning a bare `str` type-checks
                # everywhere and then `list(...)` spreads it one CHARACTER per violation
                # -- dozens of single-letter "violations" fed into the retry prompt as
                # gate feedback, with the actual complaint unreadable inside them and a
                # second LLM call spent on it. Refuse instead, naming the renderer: a
                # contract this loop cannot enforce statically is one it has to enforce
                # here, and a quiet wrong result is the bug class this codebase removes.
                # `tuple` is accepted as well as `list` -- both are the intended shape,
                # and the harmful cases are `str`/`bytes`, which the check excludes.
                #
                # The CONTAINER check alone let a non-str ELEMENT through -- `[None]`
                # passes `isinstance(reported, (list, tuple))` and then extends straight
                # into `violations`, so a broken renderer that returns e.g. `[None]`
                # feeds `None` into the retry prompt build below rather than being
                # refused here where the cause is still traceable to the renderer.
                if (not isinstance(reported, (list, tuple))
                        or not all(isinstance(item, str) for item in reported)):
                    raise TypeError(
                        f"renderer {type(renderer).__name__}.precheck returned "
                        f"{type(reported).__name__}, not a list/tuple of str -- see the "
                        f"Renderer seam's contract in sluice/core/protocols.py")
                violations = violations + list(reported)
            # The BLOCKING tier, unscoped over the whole document (see slop.check_hard):
            # an em dash in an employer line is always fixable without inventing anything.
            slop_err = _slop_hard(cv_text)
            hard_msgs = violations + [f"SLOP {lbl}: {snip}" for _ln, lbl, snip in slop_err]
            # The STYLE tier, SCOPED (#167). `section_spans` is the gate's own split, so
            # these are exactly the lines validate() reasons about -- the candidate's own
            # PROSE, where a phrase can be reworded from the same facts. Every other line
            # (employer, dates, certificate, education) is deliberately out of scope: the
            # only way to answer "SLOP <phrase>" about an employer NAME is to rename the
            # employer, which turns a style rule into fabrication pressure. slop.py takes
            # LINES and has no opinion about which ones, so this scoping is the engine's
            # job and exists nowhere else. Called again here rather than threaded out of
            # `_validate` -- it is pure and does no I/O, and that keeps validate()'s
            # signature the list-of-strings the whole gate is built on.
            #
            # Merged into ONE line-ordered pass over the union, mirroring `validate`'s own
            # merge for `validate`'s own stated reason: a CV that repeats `PROFILE` after
            # `WORK EXPERIENCE` puts a line in BOTH lists (see section_spans), and this
            # list is handed to the composer VERBATIM -- a duplicated or out-of-order
            # complaint is what the model reads.
            profile_lines, work_lines = section_spans(cv_text)
            scoped_lines = sorted(dict(profile_lines + work_lines).items())
            style_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip
                          in _slop_phrases(scoped_lines, allow=cvcfg.slop_allow)]
            # The SAME scoped lines the deterministic half of the STYLE tier just read,
            # rejoined into a document for the model to judge (#167). Both halves of one
            # tier must see one set of lines: the scoping above exists because a style
            # complaint naming an EMPLOYER or CERTIFICATE line is answerable only by
            # renaming the employer or the certificate, and that is true of a MODEL's
            # complaint about the line exactly as it is of a phrase match on it. Handing
            # `check_phrases` the scoped subset and `run_voice` the whole document would
            # leave the model-judged half free to drive a retry on a line the gate
            # deliberately put out of reach -- the scoping is a property of the TIER, not
            # of the phrase list.
            #
            # Composed HERE rather than by passing lines into cv/voice.py, for the same
            # reason cv/slop.py takes lines it has no opinion about: the PROFILE/WORK
            # split is POLICY, it belongs to the module that owns the tier, and
            # `voice.py` stays import-free. It is built from `scoped_lines` -- the merged,
            # line-ordered union -- and not from `profile_lines + work_lines`, so a CV
            # that repeats `PROFILE` after `WORK EXPERIENCE` (see section_spans) does not
            # show the model the same line twice.
            scoped_text = "\n".join(text for _ln, text in scoped_lines)
            voice_flags = []
            if not hard_msgs:
                # Model-judged VOICE check (#167, cv/voice.py): a fixed phrase list
                # cannot catch a novel AI-tell clause, which is the issue's own point
                # about the deterministic tier above. Gated twice over: `voice_check`
                # (opt-in, default False -- an unconfigured install spends no extra
                # LLM call) AND `not hard_msgs` -- there is no point spending a call
                # judging the voice of a draft about to be recomposed for citation
                # reasons anyway.
                #
                # Fails OPEN, exactly as the fabrication audit below does: a backend
                # error or timeout must not make this gate HARDER than the check that
                # actually ran. Swallow and log -- never propagate, and never let a
                # dead backend turn a style-clean, voice-untested draft into a lost
                # lead.
                #
                # `scoped_text.strip()` is the THIRD gate on the call, and the same
                # spend argument as the two above: a draft whose PROFILE and WORK
                # regions are both empty (or blank) has no candidate prose in it at
                # all, so there is nothing for a VOICE judgment to be about -- and a
                # finding returned against an empty document could name nothing in the
                # CV, while still costing the draft its one retry.
                if cvcfg.voice_check and scoped_text.strip():
                    try:
                        _report, voice_flags = run_voice(backend, scoped_text)
                    except Exception as e:
                        _log.warning("voice check for %s failed (%s); treating as "
                                     "clean", note.ref, e)
                        voice_flags = []
                best = (cv_text, style_msgs, voice_flags)
                if not style_msgs and not voice_flags:
                    break
            # ALL THREE tiers reach the composer. A style or voice finding is worth one
            # retry -- it is the whole of #167's complaint that these matches were
            # computed and thrown away -- and the retry is bounded at one either way:
            # `range(2)`. The VOICE prefix mirrors the SLOP one immediately above:
            # both are read by the same model, in the same retry prompt, and need to
            # look like the same kind of instruction to it.
            retry_msgs = hard_msgs + style_msgs + [f"VOICE: {f}" for f in voice_flags]

        backend_used = getattr(backend, "last_backend", None)
        if best is None:
            # No attempt was EVER hard-clean, which is the same fact the pre-#167 loop
            # tested for: it broke on the first clean attempt, so a non-empty gate list
            # here meant every attempt had failed. `violations`, `slop_err` and
            # `style_msgs` still describe the LAST attempt, exactly as `violations`
            # and `slop_err` did before -- this branch is reached only when no draft
            # was ever worth retaining, so there is no other attempt they could
            # sensibly describe.
            #
            # Both slop TIERS, reformatted to match `hard_msgs`'s own "SLOP <label>:
            # <snippet>" shape (Task 16) rather than the bare snippet `slop_err`
            # carried before this task gave the field a reader -- a caller printing
            # `r.slop` should not have to know which tier produced which entry.
            # `voice_flags` is passed too, for symmetry with every other CvResult
            # constructor call below: it is always `[]` here, because the voice check
            # (cv/voice.py) runs only inside the `if not hard_msgs:` branch above, and
            # `best is None` means that branch never ran to completion on any attempt.
            return CvResult(note.ref, "skipped-gate", violations=violations,
                            slop=[f"SLOP {lbl}: {snip}" for _ln, lbl, snip in slop_err]
                                 + style_msgs,
                            voice_flags=voice_flags, backend=backend_used,
                            dossier_failed=dossier_failed)
        # REBIND, before ANYTHING below reads `cv_text`. It is bound to the LAST attempt,
        # which on the sequence [attempt 1 hard-clean, attempt 2 hard-dirty] is a draft
        # that never cleared the gate. Everything past this line reads it -- `run_audit`
        # just below, `renderer.render` further down -- and the audit's flags drive
        # `unsupported_claims` -> `hold_for_signoff` -> the WITHHELD `tailored_cv`. Rebind
        # for only some of them and the engine renders one draft while auditing another: a
        # fabricated claim in the SERVED CV goes un-held, `set_tailored_cv` writes it
        # send-ready, and the run reports `rendered / audit flags: 0`. Rebind for none and
        # the renderer -- which validates nothing itself -- is handed the HARD-dirty
        # draft. ONE assignment covers every reader precisely because they all read this
        # one NAME; keep it that way rather than passing `best[0]` at a call site, which
        # is what would let a reader added later quietly miss it.
        #
        # `style_msgs` and `voice_flags` are rebound with it, and have to be: each
        # describes the retained draft and no other, so anything that gives a surviving
        # style or voice finding a consequence (Task 15's `cv.style_hold`, Task 16's
        # `CvResult.voice_flags`) must read the retained TRIPLE. Taking either from
        # whatever the loop's last iteration left behind would be the identical defect,
        # one line up.
        cv_text, style_msgs, voice_flags = best

        # The audit is advisory only (see audit.py: "NEVER blocks"). A backend error or
        # timeout here must not prevent a CV that already passed the HARD gate from
        # rendering -- swallow and log, never propagate.
        try:
            _report, audit_flags = run_audit(backend, cv_text, bundle_text)
        except Exception as e:
            _log.warning("advisory audit failed for %s: %s", note.ref, e)
            audit_flags = []
        if dry_run:
            # `slop=style_msgs, voice_flags=voice_flags` here and at every remaining
            # CvResult(...) call below (Task 16): both describe the RETAINED draft (the
            # rebind above), the same one `style_blockers` reads a few lines down for
            # `cv.style_hold` -- a dry run reports what a real run would have found, not
            # an empty placeholder. The HARD slop tier is not repeated here because it is
            # empty by construction on this path (see `slop`'s own field comment).
            return CvResult(note.ref, "dry-run", slop=style_msgs, voice_flags=voice_flags,
                            audit_flags=audit_flags, backend=backend_used,
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
        #
        # A surviving STYLE finding earns the SAME consequence under `cv.style_hold`
        # (#167, Task 15) -- deliberately a SEPARATE gate, not folded into
        # `require_signoff`: that flag's True default was chosen for FABRICATION, and
        # riding it would mean an unconfigured install withholds tailored_cv on ~40
        # case-insensitive stems out of the box (see CvConfig.style_hold's own comment).
        # `style_msgs` and `voice_flags` both describe the RETAINED draft (the rebind
        # above), and both are the STYLE tier (slop phrase matches plus the opt-in
        # model-judged voice check, Task 14) -- a voice-only finding must hold exactly
        # like a slop-phrase one, so both lists feed this the same way. One inherited
        # note from Task 13: the loop keeps the LAST hard-clean draft, so a retry that is
        # hard-clean but carries MORE style findings than a cleaner attempt 1 supersedes
        # it here too -- not a safety issue (both cleared the hard gate), but it means
        # this hold can fire on a draft that was not the least-style-dirty one composed.
        #
        # Each finding becomes its own entry in the SAME flat claims array the
        # fabrication hold already writes -- hold_for_signoff's `claims` parameter stays
        # the Store protocol's plain JSON ARRAY (core/protocols.py; not widened), and
        # core/app.py reads it back as `parsed if isinstance(parsed, list) else
        # [str(parsed)]`. A wrapped `{"kind": ..., "claims": [...]}` object would
        # therefore collapse into ONE bogus claim string -- the kind has to live on each
        # ENTRY instead, as a "style\t" prefix. An entry with NO such prefix is exactly
        # the shape every hold stamped before this change used (a raw audit verdict
        # line, e.g. "unsupported\t..."), and sluice/cli.py's sign-off prompt keeps
        # today's wording for it unchanged -- a pre-existing hold must not be
        # re-described by this upgrade.
        style_blockers = ([f"style\t{msg}" for msg in style_msgs + voice_flags]
                          if cvcfg.style_hold else [])
        blockers = (
            (unsupported_claims(audit_flags) if cvcfg.require_signoff else [])
            + style_blockers)
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
                return CvResult(note.ref, "skipped-has-cv", slop=style_msgs,
                                voice_flags=voice_flags, audit_flags=audit_flags,
                                backend=backend_used, dossier_failed=dossier_failed)
            return CvResult(note.ref, "needs-signoff", slop=style_msgs,
                            voice_flags=voice_flags, audit_flags=audit_flags,
                            served=served, backend=backend_used, dossier_failed=dossier_failed)
        if served:
            wrote = vault.set_tailored_cv(
                note.ref, f"{served} ({date.today().isoformat()})",
                only_if_absent=guard_existing_cv)
            if guard_existing_cv and not wrote:
                # A CV appeared for this lead during our compose+render window; do not clobber
                # it. The served PDF we rendered is left in served_dir (it passed the gate);
                # only the note pointer is withheld. See #16 cv long-window.
                return CvResult(note.ref, "skipped-has-cv", slop=style_msgs,
                                voice_flags=voice_flags, audit_flags=audit_flags,
                                backend=backend_used, dossier_failed=dossier_failed)
        return CvResult(note.ref, "rendered", slop=style_msgs, voice_flags=voice_flags,
                        audit_flags=audit_flags, served=served, backend=backend_used,
                        dossier_failed=dossier_failed)
    except Exception as e:
        e.dossier_failed = dossier_failed
        raise


def run_batch(vault, cvcfg, backend, dossier_cache, *, renderer, limit=None,
              dry_run=False, policy=StalenessPolicy()) -> list:
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
