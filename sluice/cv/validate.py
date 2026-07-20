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
# NB this NARROWS the free-text bypass rather than closing it -- a body line that
# happens to look like a real code is still read as one. See test_an_id_shaped_
# bracket_in_free_text_is_still_a_citable_id, which pins that bound. (#31)
_ID_RE = re.compile(r"^\[([A-Z]{2}\d+)\]")


def _bundle_ids_and_nums(bundle_text):
    ids, nums = {}, {}
    cur = None
    for line in bundle_text.splitlines():
        if _SECTION_RE.match(line):
            cur = None                      # this entry's lines have ended
            continue
        m = _ID_RE.match(line)
        if m:
            cur = m.group(1)
            ids[cur] = line
            after = line[m.end():]          # exclude the id token itself
            nums[cur] = set(re.findall(r"\d+", after))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
    return ids, nums


def validate(cv_text, bundle_text, employers=None, fabrication_decoys=None):
    v = []
    ids, nums = _bundle_ids_and_nums(bundle_text)
    for decoy in (fabrication_decoys or []):
        if decoy.lower() in cv_text.lower():
            v.append(f"FABRICATED: contains '{decoy}'")
    for e in (employers or []):
        if e not in cv_text:
            v.append(f"MISSING EMPLOYER: {e}")
    years = [int(y) for y in re.findall(r"\d{2}/(\d{4})\s*[–-]", cv_text.split("WORK EXPERIENCE")[-1])]
    if years != sorted(years, reverse=True):
        v.append(f"NOT REVERSE-CHRONOLOGICAL: start years {years}")
    in_work = False
    for line in cv_text.splitlines():
        u = line.strip().upper()
        if u == "WORK EXPERIENCE":
            in_work = True
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work = False
        # Must match cv_render_v2.py's bullet markers exactly -- the renderer treats
        # '-', '•', and '*' all as bullets, so a WORK bullet composed with '•' or '*'
        # is delivered in the rendered PDF and MUST be citation-checked here too.
        if in_work and line.lstrip().startswith(("-", "•", "*")):
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
