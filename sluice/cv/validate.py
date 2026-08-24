# sluice/cv/validate.py
"""Deterministic citation/fabrication gate. Pure: validate(cv_text, bundle_text) ->
[violations]. A non-empty list HARD-blocks rendering. Every WORK bullet must cite a
real bundle [id]; every number in a bullet must appear in a cited entry.

`employers` and `fabrication_decoys` are supplied by the caller (from CvConfig, see
cv/config.py) rather than hardcoded here: an empty `employers` list skips the
per-employer completeness check (so the gate works for anyone, not just a caller
with a fixed employer roster), and an empty `fabrication_decoys` list skips the
known-hallucination-string check. Configure both via the `cv:` block of
sluice.yaml for the same behavior a fixed list gave before."""
import re

# render_bundle emits `=== SECTION ===` headers; NEGATIVE CONSTRAINTS is the one that
# lands AFTER the last [id]. Without a reset its lines fall through to `elif cur` and
# union the do-not-say numbers into the last-ranked entry's allowlist -- so the one
# guard aimed at those figures stops firing on exactly them. The `[^=]` guards require
# a non-'=' character inside each delimiter, so a bare setext underline (`======`) in a
# pasted entry body does not reset. A body line genuinely shaped like a section header
# still would, and that fails CLOSED: the entry's later numbers drop and a legitimate
# bullet is flagged INVENTED METRIC -- visible and fixable, never a silent pass. (#31)
_SECTION_RE = re.compile(r"^\s*={3,}[^=].*[^=]={3,}\s*$")

# assign_codes/_prefix guarantee exactly two A-Z letters plus a sequence number, so
# anchoring to that shape costs nothing real and closes a second widening: `body` and
# `baseline` are user free text spliced into the bundle verbatim, and an unanchored
# bracket match turned a line like `[2019] Rebuilt the pipeline to 250 nodes` into a
# citable id owning 250 -- a bullet could cite a YEAR and carry a fabricated figure
# past the gate. Fails CLOSED: a bullet citing an id this rejects is now an unknown
# citation, which is already a BAD CITATION violation. cv/render.py's _CITE_RE has
# assumed this same shape all along, so this aligns the parser with the strip step.
# NB this NARROWS the free-text bypass rather than closing it. A body line shaped
# like a real code is still read as one, and if it matches an EARLIER entry's code
# it OVERWRITES that entry's numbers (`nums[cur] = ...` below, not `|=`) -- so the
# fabricated figure passes AND the entry's genuine metric is reported INVENTED.
# Both halves are pre-existing, measured, and pinned by tests rather than assumed;
# the real close is handing validate() the true id list, a signature change. (#174 --
# NOT #31, which this line used to cite: #31 is closed, and was the NEGATIVES-block
# reset above, a different defect. `core/vault.py`'s `_refuse_citation_shaped_body`
# narrows the same bypass from the write side and cites #174; the two now name one
# issue. "The write side" is BOTH evidence writes -- propose AND verify. It used to be
# propose alone, which was not the narrowing this comment claimed: an entry hand-placed
# in `_inbox/` never passes through propose, and one carrying `[NC1] delivered 987
# things` verified cleanly and landed citable (#164 review, M1). The comment was widened
# by widening the GUARD, not by narrowing the claim.)
_ID_RE = re.compile(r"^\[([A-Z]{2}\d+)\]")

# The PROFILE is prose, not bullets, so its citation strip must match what the
# RENDERER delivers, not the WORK-bullet strip. render.strip_citations removes only
# id-shaped [XX9] codes (render._CITE_RE), so a NON-id bracket like [500] SURVIVES
# into the PDF and the profile check must see and check it. This pattern is
# byte-identical to render._CITE_RE (render.py:10); test_profile_strip_matches_render_
# citation_shape pins that equality, because a comment cannot enforce it and a drift
# silently reopens a fabricated-number-ships fail-open. Deliberately DISTINCT from
# _ID_RE: _ID_RE parses bundle-GENERATED codes (uppercase via _prefix); _CITE_RE
# mirrors render's LENIENT strip of whatever the model emitted ([A-Za-z]). (#30)
_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")


def _bundle_ids_and_nums(bundle_text):
    ids, nums, baseline = {}, {}, set()
    cur = None
    seen_id = False
    for line in bundle_text.splitlines():
        if _SECTION_RE.match(line):
            cur = None                      # this entry's lines have ended
            continue
        m = _ID_RE.match(line)
        if m:
            seen_id = True
            cur = m.group(1)
            ids[cur] = line
            after = line[m.end():]          # exclude the id token itself
            nums[cur] = set(re.findall(r"\d+", after))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
        elif not seen_id:
            # Numbers before the first [id] are the BASELINE block -- a permitted
            # SOURCE for the PROFILE (an aggregate summary) but NOT for a WORK bullet,
            # which must trace to its specific cited entry. Negatives are NOT captured
            # here: they land after the last [id] (seen_id True, cur cleared by their
            # === header ===), so they fall into neither pool and stay excluded -- the
            # same exclusion #31 established for bullets. (#30)
            baseline |= set(re.findall(r"\d+", line))
    return ids, nums, baseline


def section_spans(cv_text):
    """The PROFILE prose lines and the WORK bullet lines of `cv_text`, 1-indexed.

    Returns `(profile_lines, work_bullet_lines)`, each a list of `(lineno, line)` with
    the RAW line: both checks below consume it unstripped (`_CITE_RE.sub("", line)`,
    `line.lstrip()`), so handing back a stripped line would change what they see.

    Extracted from `validate`'s own loop so a later scope-limited check can reason about
    the exact lines the gate reasons about, rather than a second copy of the split that
    would drift from it. `validate` consumes this too -- one state machine, two
    consumers. `tests/test_cv_validate.py`'s equivalence pin transcribes the loop as it
    stood BEFORE the extraction and asserts these two lists against it.

    The terminator set is `CERTIFICATES`/`EDUCATION` and NOTHING else, deliberately. The
    obvious generalisation -- "any all-caps line ends the section" -- is a WEAKENING of
    the fabrication gate, not a tidy-up: bullets under a header this module does not
    model (PUBLICATIONS, PROJECTS, AWARDS) are citation-checked today, and a generic
    splitter would silently stop checking them, rendering an uncited claim into the PDF.
    That is the arm a naive extraction drops, so it is pinned by name.

    Note that neither header CLEARS the other's flag on the way in: `PROFILE` sets
    `in_profile` without touching `in_work`, so a CV that repeats `PROFILE` after
    `WORK EXPERIENCE` puts the same line in BOTH lists. `validate` reports in line order
    across the two for that reason, and that order is pinned too -- the violation list is
    fed back to the composer verbatim on its single retry.

    The marker tuple is the gate's own and must stay the ONLY `startswith((...))` in
    this module -- `tests/test_cv_parse.py` reads it out of this module's AST (see
    `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks`, which
    refuses to pass if it finds more than one) and asserts EQUALITY with `cv/parse.py`'s
    `_BULLET_MARKERS`, because a WORK marker the parser accepts and the gate does not
    reaches the PDF with the citation check never having looked at it. It is NOT that
    module's `_TRAILING_MARKERS`, which is deliberately WIDER and scoped to
    CERTIFICATES/EDUCATION -- sections this helper collects nothing inside, since the
    terminator clears both flags and only a later PROFILE/WORK EXPERIENCE header can set
    them again (see the paragraph above: a terminator is not permanent, it holds until
    the next modelled header). CLAUDE.md is explicit the two tuples stay separate rather
    than being widened into one.
    """
    profile, work = [], []
    in_work = False
    in_profile = False
    for i, line in enumerate(cv_text.splitlines(), 1):
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile = True
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile = False, False
        if in_profile:
            profile.append((i, line))
        if in_work and line.lstrip().startswith(("-", "•", "*")):
            work.append((i, line))
    return profile, work


def validate(cv_text, bundle_text, employers=None, fabrication_decoys=None):
    v = []
    ids, nums, baseline = _bundle_ids_and_nums(bundle_text)
    for decoy in (fabrication_decoys or []):
        if decoy.lower() in cv_text.lower():
            v.append(f"FABRICATED: contains '{decoy}'")
    for e in (employers or []):
        if e not in cv_text:
            v.append(f"MISSING EMPLOYER: {e}")
    years = [int(y) for y in re.findall(r"\d{2}/(\d{4})\s*[–-]", cv_text.split("WORK EXPERIENCE")[-1])]
    if years != sorted(years, reverse=True):
        v.append(f"NOT REVERSE-CHRONOLOGICAL: start years {years}")
    # Permitted numbers for PROFILE prose: any figure the bundle actually contains as
    # a source -- every entry's allowlist plus the baseline block -- but NOT the
    # negatives (excluded by the parse). Broader than a WORK bullet, which is tied to
    # its cited entry, because a profile is an aggregate summary. (#30)
    profile_permitted = baseline.union(*nums.values())
    profile_lines, work_bullets = section_spans(cv_text)
    # The two line SETS come from `section_spans` so a later scope-limited check (#167)
    # can share this split instead of keeping a second copy that would drift from it; the
    # two checks below are unchanged. Merged back into ONE line-ordered pass, not two
    # sequential passes, because both regions can be live at once (`PROFILE` after
    # `WORK EXPERIENCE` -- see `section_spans`) and the violation list's order is output:
    # it is fed back to the composer verbatim on the single retry.
    profile_by_line = dict(profile_lines)
    work_by_line = dict(work_bullets)
    for i in sorted(profile_by_line | work_by_line):
        if i in profile_by_line:
            line = profile_by_line[i]
            # Prose, NOT a bullet: no citation required or expected (requiring [id]
            # on prose invites a fake-citation launder). Strip citations with render's
            # EXACT shape (_CITE_RE) so the check sees what the reader sees -- narrower
            # than the WORK strip on purpose: a non-id bracket like [500] survives to
            # the PDF and MUST be checked, and the profile has no BAD-CITATION backstop
            # behind the strip. (#30)
            prose = _CITE_RE.sub("", line)
            for n in re.findall(r"\d+", prose):
                if n not in profile_permitted:
                    v.append(f"INVENTED PROFILE METRIC {n} not in bundle: {prose.strip()[:50]}")
        # The bullet-marker test that selects these lines lives in `section_spans` and
        # must match cv_render_v2.py's bullet markers exactly -- the renderer treats
        # '-', '•', and '*' all as bullets, so a WORK bullet composed with '•' or '*'
        # is delivered in the rendered PDF and MUST be citation-checked here too.
        if i in work_by_line:
            line = work_by_line[i]
            cites = re.findall(r"\[([^\]]+)\]", line)
            if not cites:
                v.append(f"UNCITED BULLET: {line.strip()[:60]}")
                continue
            bad = [c for c in cites if c not in ids]
            if bad:
                v.append(f"BAD CITATION {bad}: not bundle entries - {line.strip()[:50]}")
                continue
            prose = re.sub(r"\[[^\]]+\]", "", line)
            bullet_nums = set(re.findall(r"\d+", prose))
            union = set().union(*(nums[c] for c in cites))
            invented = bullet_nums - union
            if invented:
                v.append(f"INVENTED METRIC {sorted(invented)} not in {cites}: {prose.strip()[:50]}")
    return v
