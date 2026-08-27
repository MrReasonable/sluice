# tests/test_cv_validate.py
import random

import pytest

from sluice.cv.bundle import build_bundle, bundle_sources
from sluice.cv.validate import section_spans, validate

# Sample employer roster / decoy list a caller (CvConfig.employers /
# CvConfig.fabrication_decoys) would supply; validate() itself ships with no
# hardcoded employers or decoys, so tests exercising those gates pass them in.
EMPLOYERS = ["Example Systems", "Example Analytics", "Example Robotics",
             "Example Cartography"]
FABRICATION_DECOYS = ["Example Decoy"]

# Built through the real bundle pipeline rather than by hand, so validate() is
# exercised against the same BundleSources build_bundle's callers actually produce
# rather than a hand-assembled stand-in that could quietly drift from it. Ids are
# EF1/ET1/ET2 (assign_codes sequences per prefix from 1); `jd_keywords=[]` and an
# explicit prefix_map are both required to make them deterministic, because
# build_bundle ranks before it assigns codes.
_LEGACY_ENTRIES = [
    {"company": "Example Foundry", "title": "EM", "metrics": "3 8",
     "best_for": "", "category": ""},
    {"company": "Example Telemetry", "title": "CTO", "metrics": "90 99",
     "best_for": "", "category": ""},
    {"company": "Example Telemetry", "title": "Lead", "metrics": "15",
     "best_for": "", "category": ""},
]
BUNDLE = bundle_sources(build_bundle(
    entries=_LEGACY_ENTRIES, baseline="Baseline prose.",
    negatives=["never claim 400 users"], jd_keywords=[],
    prefix_map={"Example Foundry": "EF", "Example Telemetry": "ET"}))

def _cv(work):
    L = ["Phone number: +00 000", "Email address: x@y", "Web: https://x", "", "JANE ROE",
         "", "PROFILE", "I lead.", "", "WORK EXPERIENCE", ""]
    for co, dl, bs in work:
        L += [co, dl] + bs + [""]
    L += ["CERTIFICATES", "- Example Scrum Master", "", "EDUCATION", "- Uni"]
    return "\n".join(L)

# Synthetic throughout. Only the descending start years matter to the gate
# (validate()'s `\d{2}/(\d{4})\s*[–-]` chronology check); the count, roles, cities
# and employers are arbitrary.
FULL = [
    ("Example Systems", "02/2023–present | Example Location A | Staff Engineer", ["- Shipped it [EF1]"]),
    ("Example Analytics", "06/2020–01/2023 | Example Location B | Senior Engineer",
     ["- Grew team from 3 to 8 [EF1]"]),
    ("Example Robotics", "09/2017–05/2020 | Example Location C | Engineer", ["- Coached [EF1]"]),
    ("Example Cartography", "07/2015–08/2017 | Example Location A | Junior Engineer", ["- CI [EF1]"]),
]

def test_clean_passes():
    assert validate(_cv(FULL), BUNDLE, employers=EMPLOYERS,
                    fabrication_decoys=FABRICATION_DECOYS) == []

def test_employer_and_decoy_gates_are_off_by_default():
    # With no employers/fabrication_decoys configured (the neutral default),
    # a CV missing employers or naming a decoy is not flagged: the gate only
    # runs when the caller supplies a list.
    assert validate(_cv(FULL[:-1]), BUNDLE) == []
    f = [x[:] for x in FULL]
    f[0] = ("Example Systems", "02/2023–present | Example Location A | Staff Engineer", ["- Built at Example Decoy [EF1]"])
    assert validate(_cv(f), BUNDLE) == []

def test_id_digits_not_counted_as_metric():
    # The `1` in [ET1] is part of the citation CODE, not a metric of that entry --
    # `bundle_sources` slices the leading `[{id}] ` token off by LENGTH before
    # scanning for digits, for that reason (cv/bundle.py's `block[0][len(eid) + 2:]`).
    #
    # The first assertion below is the one this test shipped with, and it was
    # INERT: a digit-free bullet leaves bullet_nums empty, so `invented` is empty
    # whatever the id token contributes, and the assertion held under every
    # mutation. It was already inert before the render_bundle port; running the
    # port's test->mutation pairs is what surfaced it. Kept (it still pins that a
    # digit-free citing bullet is clean) and paired with the load-bearing half.
    f = [x[:] for x in FULL]
    f[3] = ("Example Cartography", "07/2015–08/2017 | Example Location A | Junior Engineer",
            ["- Owned direction [ET1]"])
    assert validate(_cv(f), BUNDLE) == []

    # `1` appears ONLY inside the id token [ET1]; ET1's metrics are 90 and 99. If
    # the parser scanned the id token too, the code's own digits would silently
    # become permitted figures, so this bullet MUST be flagged.
    f[3] = ("Example Cartography", "07/2015–08/2017 | Example Location A | Junior Engineer",
            ["- Owned 1 direction [ET1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_multi_citation_union():
    f = [x[:] for x in FULL]
    f[3] = ("Example Cartography", "07/2015–08/2017 | Example Location A | Junior Engineer",
            ["- Lifted uptime 90 to 99 across a 15-person team [ET1] [ET2]"])
    assert validate(_cv(f), BUNDLE) == []

def test_invented_metric_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Example Location B | Senior Engineer", ["- Grew team from 3 to 23 [EF1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_uncited_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Example Location B | Senior Engineer", ["- Grew team from 3 to 8"])
    assert any("UNCITED" in x for x in validate(_cv(f), BUNDLE))

def test_missing_employer_flagged():
    assert any("MISSING EMPLOYER" in x for x in
               validate(_cv(FULL[:-1]), BUNDLE, employers=EMPLOYERS))

def test_decoy_flagged():
    f = [x[:] for x in FULL]
    f[0] = ("Example Systems", "02/2023–present | Example Location A | Staff Engineer", ["- Built at Example Decoy [EF1]"])
    assert any("Example Decoy" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_decoy_case_insensitive_flagged():
    # lowercase/mixed-case "example decoy" must not slip past a case-sensitive check.
    f = [x[:] for x in FULL]
    f[0] = ("Example Systems", "02/2023–present | Example Location A | Staff Engineer", ["- Built at example decoy [EF1]"])
    assert any("FABRICATED" in x or "Example Decoy" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_bullet_marker_uncited_flagged():
    # cv_render_v2.py (the real renderer) treats '-', '•', and '*' all as bullet
    # markers, so a WORK bullet composed with '•' is rendered into the delivered
    # PDF exactly like a '-' bullet. Against the pre-fix code (which only detected
    # '-') this bullet was invisible to the gate -- no violation was raised, so
    # a fabricated/uncited claim would sail through. It must be caught here too.
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Example Location B | Senior Engineer", ["• Grew team from 3 to 8"])
    assert any("UNCITED" in x for x in validate(_cv(f), BUNDLE))


# --- Fixtures for the section-boundary and id-anchor guards (#31) -------------
# BUNDLE above covers the legacy assertions; these add the regions it has no
# reason to exercise -- entry bodies, a baseline block, and negatives carrying
# digits -- because those are where the two defects #31 fixes actually lived.

_PREFIX_MAP = {"Example Systems": "ES", "Example Analytics": "EA"}

_ENTRIES = [
    {"company": "Example Systems", "title": "Engineer", "metrics": "90",
     "body": "Ran 42 services in the platform group.", "best_for": "", "category": ""},
    {"company": "Example Analytics", "title": "Analyst", "metrics": "12",
     "body": "Owned 8 dashboards.", "best_for": "", "category": ""},
]


def _bundle(entries=None, baseline="Baseline prose, no digits.", negatives=None):
    # jd_keywords=[] AND an explicit prefix_map are BOTH required for stable ids:
    # build_bundle ranks before assign_codes, so ranking decides the numbering.
    # With no keywords every entry scores 0 and the (stable) sort preserves order,
    # giving ES1 then EA1 -- so EA1 is the last-ranked entry the negatives block
    # would otherwise be attributed to.
    return bundle_sources(build_bundle(
        entries=_ENTRIES if entries is None else entries,
        baseline=baseline,
        negatives=[] if negatives is None else negatives,
        jd_keywords=[], prefix_map=_PREFIX_MAP))


def _work_cv(*bullets):
    return "\n".join(["JANE ROE", "", "PROFILE", "I build things.", "",
                      "WORK EXPERIENCE", "",
                      "Example Systems", "02/2023–present | Example Location A | Staff Engineer",
                      *bullets, "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])


def _cv_with_profile(profile, *bullets):
    # A CV whose PROFILE line is caller-controlled, over the _ENTRIES bundle
    # (ES1 metrics=90 body "Ran 42 services…"; EA1 metrics=12 body "Owned 8
    # dashboards."). Default bullet is clean (42 is in ES1's body). One WORK entry,
    # so the reverse-chronology check sees a single start year and passes.
    return "\n".join(["JANE ROE", "", "PROFILE", profile, "",
                      "WORK EXPERIENCE", "",
                      "Example Systems", "02/2023–present | Example Location A | Staff Engineer",
                      *(bullets or ["- Ran 42 services [ES1]"]), "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])


def test_negatives_block_does_not_widen_the_last_entrys_allowlist():
    # The exclusion is now STRUCTURAL, not positional: `bundle_sources` builds each
    # entry's allowlist only from that entry's own `_entry_block` lines and never
    # reads `bundle["negatives"]` at all (cv/bundle.py's own docstring states this).
    # There is no longer a text position a negative could occupy that would
    # attribute it to an entry. That is NOT "no deletion mutant at all" -- deleting
    # validate()'s INVENTED-METRIC arm outright turns this test red like any other
    # INVENTED-METRIC assertion. The narrower, true claim: no mutant SPECIFIC to the
    # negatives exclusion survives, because re-widening it needs a line ADDED to
    # `bundle_sources` (reading `bundle["negatives"]`) -- an ADD-shaped mutant, which
    # this suite's mutation discipline (move-or-delete only, CLAUDE.md) forbids.
    # Kept as a regression pin on the OUTCOME: a bullet citing EA1 must still never
    # carry 500 (present only in the negatives list) past the gate.
    b = _bundle(negatives=["never claim 500 users"])
    v = validate(_work_cv("- Scaled the platform to 500 users [EA1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_a_body_sourced_number_stays_permitted():
    # Guards the opposite failure: a fix that narrows the allowlist to nothing.
    # The number MUST come from the entry's body, not its metrics= line -- metrics
    # is parsed on the same line that sets `cur`, so a metrics-sourced number is
    # unreachable by any cur-clearing change and cannot detect an over-broad one.
    b = _bundle(negatives=["never claim 500 users"])
    assert validate(_work_cv("- Ran 42 services [ES1]"), b) == []


def test_baseline_numbers_are_not_permitted_in_a_bullet():
    # Pins today's behaviour rather than changing it: the baseline block precedes
    # the first [id], so its numbers are attributed to no entry. Permitting them
    # is #30's design question, and this test makes that a deliberate change.
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    v = validate(_work_cv("- Led 777 deployments [ES1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_a_bracket_led_body_line_is_not_a_citable_id():
    # `body` and `baseline` are user free text spliced into the bundle verbatim.
    # An unanchored bracket match used to turn any bracket-led line into a citable
    # id, so a bullet could cite a YEAR and inherit whatever numbers followed it.
    # #174 closes the id half of that by construction: citable ids now come from
    # `assign_codes`' real output (BundleSources.ids), never from re-scanning
    # rendered text, so a body line can never mint one no matter its shape -- this
    # test's own [2019] citation (not even id-shaped) is refused as a BAD CITATION
    # regardless of what `body` contains. That does not make this test inert:
    # measured, deleting validate()'s BAD-CITATION arm (the `c not in ids` check
    # and its `v.append`) turns it red. It is no longer the SOLE witness on that
    # arm, though -- the sibling below, `..._is_not_a_citable_id`, cites the
    # id-shaped but nonexistent [QQ7] and dies on the identical mutation, because
    # both citations fail the same structural membership check once ids stop
    # being recovered from text: whether a rejected citation happens to look
    # id-shaped no longer changes which code path rejects it. Kept anyway as the
    # narrower pin -- a non-id-shaped bracket is the exact shape the original #31
    # defect exploited, and that history is worth keeping visible even though the
    # coverage now overlaps a sibling.
    e = [dict(_ENTRIES[0], body="[2019] Rebuilt the pipeline to 250 nodes"), _ENTRIES[1]]
    v = validate(_work_cv("- Ran 250 nodes [2019]"), _bundle(entries=e))
    assert any("BAD CITATION" in x for x in v), v


def test_an_id_shaped_bracket_in_free_text_is_not_a_citable_id():
    # CLOSED by #174 (was a CHARACTERISATION of a residual, not desired behaviour).
    # Before #174, validate() recovered citable ids by re-parsing the rendered
    # bundle TEXT, so any free-text line shaped like a real code -- even one
    # belonging to no entry at all -- was accepted as a citation token. #174
    # replaced that with a structural source set: an id is citable only if
    # `assign_codes` actually assigned it (BundleSources.ids, `nums.keys()` in
    # cv/bundle.py), so a body line that merely LOOKS like one can no longer mint
    # a citable id. [QQ7] names no real bundle entry, so citing it is now refused.
    e = [dict(_ENTRIES[0], body="[QQ7] fabricated 500 users"), _ENTRIES[1]]
    v = validate(_work_cv("- Scaled to 500 users [QQ7]"), _bundle(entries=e))
    assert any("BAD CITATION" in x for x in v), v


def test_an_id_shaped_line_in_a_later_body_no_longer_shadows_the_real_entry():
    # CLOSED by #174. This was the sharper edge of the same residual, and worse
    # than minting a spurious id: a free-text body line shaped like an EARLIER,
    # REAL code used to OVERWRITE that entry's allowlist (`nums[cur] = ...`
    # re-triggering on the id-shaped line), so a fabricated figure passed AND the
    # entry's genuine metric was reported INVENTED -- both directions wrong at once.
    #
    # #174 derives each entry's `nums` from that entry's OWN `_entry_block` lines
    # only (cv/bundle.py's `bundle_sources`), never by re-scanning rendered text,
    # so a body line belonging to EA1 that happens to read "[ES1]" is just more of
    # EA1's own prose -- it cannot reach back and rebind ES1's allowlist. Both
    # halves now hold: the fabricated 500 (sourced only from EA1's body) is
    # refused when cited against ES1, and ES1's own genuine metric (90, from its
    # own metrics= line) still clears.
    e = [_ENTRIES[0], dict(_ENTRIES[1], body="[ES1] fabricated 500 users")]
    b = _bundle(entries=e)
    assert any("INVENTED" in x for x in validate(_work_cv("- Scaled to 500 users [ES1]"), b))
    assert validate(_work_cv("- Held 90 uptime [ES1]"), b) == []


def test_an_id_shaped_line_in_an_entrys_own_body_still_sources_that_entrys_digits():
    """The sibling above proves an id-shaped body line cannot REBIND another entry's
    allowlist. This proves the opposite direction: it must not also cost the OWNING
    entry its own digits. A real Experience Library entry can legitimately read like
    `[EF1] as above, cut latency to 250 ms` -- cross-referencing an earlier code
    informally in prose -- and 250 is a genuine metric of THIS entry, not a rebind
    attempt.

    `test_a_bracket_led_body_lines_numbers_join_the_enclosing_entry` (deleted as a
    duplicate, git 942ebbe) was the only witness against dropping id-shaped lines from
    the harvest. It was deleted as a duplicate of `test_a_body_sourced_number_stays_
    permitted` under a DIFFERENT mutant (dropping `body` from `_entry_block` outright),
    which left the id-shaped-line-specific hole uncovered: a filter added to
    `bundle_sources` that drops id-shaped lines from an entry's own body before
    harvesting -- plausible-looking as "don't let a body line masquerade as a
    citation" -- takes this entry's allowlist from `{'1', '250', '90'}` to `{'90'}`
    with the whole suite green (measured against the pre-fix suite; see this PR's
    review). The `1` is not a typo -- it is `[EA1]`'s OWN digit, the id-shaped token's
    residual this same wave's docs (docs/ARCHITECTURE.md, CLAUDE.md) document for the
    baseline case (the `9` of a stray `[ZZ9]`): the blind `\\d+` sweep that removes
    #174's three holes admits it here too, on purpose, for the identical reason. A
    truthful bullet citing 250 against its real source is then reported INVENTED
    METRIC, burning the single retry and possibly the lead.
    """
    e = [dict(_ENTRIES[0], body="[EA1] as above, cut latency to 250 ms"), _ENTRIES[1]]
    b = _bundle(entries=e)
    assert validate(_work_cv("- Cut latency to 250 ms [ES1]"), b) == []


def test_a_section_shaped_body_line_no_longer_strands_the_entrys_numbers():
    """Change 2. `_SECTION_RE` existed only to keep the negatives block off the last
    entry, and it took a genuine `=== X ===` line in an entry BODY with it: measured, the
    user's own verified figure below was reported INVENTED, costing the single retry and
    potentially the lead. The derivation has no positional parse, so the whole body counts.

    That is NOT "no deletion mutant at all" -- measured: deleting the
    `if entry.get("body"): lines.append(entry["body"])` lines from `_entry_block`
    outright also turns this test red, and it is not even a unique witness for that
    mutant -- several other tests in this file and in test_cv_bundle.py catch the
    identical drop-body mutant (test_a_body_sourced_number_stays_permitted among them),
    the same shape git 942ebbe measured before deleting this test's two duplicates. The
    narrower, true claim: no mutant SPECIFIC to the section-shape exclusion survives,
    because re-introducing a shape-based filter (like the old `_SECTION_RE` check) needs
    a line ADDED to `_entry_block`/`bundle_sources`, which this suite's mutation
    discipline (move-or-delete only, CLAUDE.md) forbids.
    """
    e = [dict(_ENTRIES[0], body="Highlights\n=== Detail ===\nCut latency to 250 ms"),
         _ENTRIES[1]]
    assert validate(_work_cv("- Cut latency to 250 ms [ES1]"), _bundle(entries=e)) == []


def test_an_id_shaped_baseline_line_no_longer_mints_a_citable_entry():
    """The second live instance of #174's class, found while scoping it and not named in
    the issue: the baseline pool accumulated only while no id had been seen, so an
    `[XX9]`-shaped line anywhere in the baseline CV minted a fully citable entry. A bullet
    citing it then carried a fabricated figure under a fabricated citation, gate-clean.

    The DERIVATION has no deletion mutant, same shape as the sibling above: nothing in
    `bundle_sources` anchors an id to the `[A-Z]{2}\\d+` shape by matching it OUT of text
    any more, so there is no check there to delete -- re-introducing the mint-a-citable-
    entry hole needs an ADD. But the test AS A WHOLE is not arm-independent: deleting
    `validate`'s own BAD-CITATION arm also kills it (measured), because the now-unrejected
    `ZZ9` citation reaches `nums[c] for c in cites` with no `ZZ9` key and raises KeyError
    rather than failing the assertion cleanly.
    """
    b = _bundle(baseline="Career summary.\n[ZZ9] stray line with 4200")
    v = validate(_work_cv("- Shipped 4200 units [ZZ9]"), b)
    assert any("BAD CITATION" in x for x in v), v


# --- Numeric floor on the PROFILE region (#30) --------------------------------

def test_invented_profile_metric_flagged():
    # 500 appears nowhere in the bundle -> flagged. The core new coverage.
    v = validate(_cv_with_profile("I scaled platforms to 500 users."), _bundle())
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_number_from_baseline_is_permitted():
    # In the PROFILE, 777 (a baseline aggregate) is permitted -- and this single
    # assertion is also the coverage for the design's change 3, the two PROFILE
    # widenings the structural derivation admits over the old positional parse: a
    # digit inside a `=== 2020 Highlights ===`-shaped BASELINE line (old code's
    # `_SECTION_RE` matched it first and `continue`d, so 2020 never reached
    # `baseline` at all) and an id-shaped baseline line's OWN digit, e.g. the '9' of
    # a `[ZZ9]` token (old code sliced the id token off before harvesting digits
    # FOR the entry it minted, and by then `seen_id` was already True so the digit
    # never reached `baseline` either way). Both are gone as separate code paths:
    # `bundle_sources`' baseline harvest is `re.findall(r"\d+", ...)` over the raw
    # block, with no shape check on any line, so a '2020' or a '9' reaches
    # `profile_permitted` by the exact same mechanism this plain '777' does. There
    # is no branch a mutant could target that would re-narrow only the two
    # shape-specific cases without also breaking this one -- a fourth test naming
    # them would carry no mutant this one does not already kill.
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    assert validate(_cv_with_profile("I led 777 deployments."), b) == []
    # ...but in a WORK BULLET the same baseline number is still flagged. Bullets
    # stay strict (the #5 divergence); assert both to make the asymmetry explicit.
    v = validate(_cv_with_profile("I build.", "- Led 777 deployments [ES1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_profile_number_from_an_entry_is_permitted():
    # 8 comes from EA1's body -> in the profile pool (union of all entries).
    assert validate(_cv_with_profile("I built 8 dashboards across teams."), _bundle()) == []


def test_profile_number_from_negatives_is_flagged():
    b = _bundle(negatives=["never claim 500 users"])
    v = validate(_cv_with_profile("I scaled to 500 users."), b)
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_at_zero_entries_the_negatives_no_longer_reach_the_profile_pool():
    """The third live instance, and it runs the other way -- a NARROWING.

    With no entries `seen_id` never set, so the NEGATIVE CONSTRAINTS block fell through
    into the baseline arm and its do-not-say figures became profile-permitted. Zero
    entries is reachable on any install before the user has written an Experience
    Library entry -- and an empty read is not a mere "no results" there, it fails the
    WORK-bullet check closed (core/vault.py:1766-1774).

    NOT gate-clean today, corrected: `- Ran things` carries no `[id]`, so `validate`
    already reports `UNCITED BULLET` for it, independent of anything this test is about.
    Measured against origin/main (pre-#174): `['UNCITED BULLET: - Ran things']`. The
    INVENTED PROFILE METRIC finding this test pins is a SECOND violation the fix starts
    reporting once the derivation stops leaking negatives into the profile pool -- the
    fix narrows what is PERMITTED, not what is already a violation for another reason.

    Has a deletion mutant, just not in `bundle_sources`: deleting `validate`'s own
    INVENTED-PROFILE-METRIC arm also kills this test (measured -- the assertion goes
    from a match to `AssertionError: ['UNCITED BULLET: - Ran things']`). The claim that
    survives is narrower: the DERIVATION never reads `bundle["negatives"]` at all, so
    re-introducing the leak needs a line ADDED to `bundle_sources`, not deleted from it.
    """
    b = _bundle(entries=[], negatives=["never claim 500 users"])
    v = validate(_cv_with_profile("I scaled to 500 users.", "- Ran things"), b)
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_decoy_flagged():
    # Characterisation: the decoy check is already GLOBAL, so a decoy in the profile
    # is flagged without new code. Guards against a future change scoping it to a region.
    v = validate(_cv_with_profile("I built systems at Example Decoy."), _bundle(),
                 fabrication_decoys=["Example Decoy"])
    assert any("FABRICATED" in x for x in v), v


def test_a_profile_id_citation_code_is_not_an_invented_metric():
    # A stray id-shaped [ES1] in the profile: render strips it, so the reader never
    # sees the '1' (which is not in the pool); the gate must not count it.
    assert validate(_cv_with_profile("I led the platform team [ES1]."), _bundle()) == []


def test_a_profile_non_id_bracketed_number_is_flagged():
    # [500] is NOT id-shaped, so render leaves it in the PDF; the reader sees 500, so
    # it must be checked. This distinguishes the narrow (render-matching) strip from
    # the broad WORK strip -- the inv-001 fail-open.
    v = validate(_cv_with_profile("I scaled to [500] users."), _bundle())
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_strip_matches_render_citation_shape():
    # The profile strip must remove exactly what the renderer removes, or a token
    # validate strips but render delivers reopens the [500] fail-open. Pin equality.
    from sluice.cv.render import _CITE_RE as _RENDER_CITE_RE
    from sluice.cv.validate import _CITE_RE as _VALIDATE_CITE_RE
    assert _VALIDATE_CITE_RE.pattern == _RENDER_CITE_RE.pattern
    assert _VALIDATE_CITE_RE.flags == _RENDER_CITE_RE.flags
    for s in ("I scaled [ES1] fast", "I scaled [500] users", "count [es1] here",
              "value [AB12] ok", "unicode [ES१] digit", "plain text"):
        assert _VALIDATE_CITE_RE.sub("", s) == _RENDER_CITE_RE.sub("", s), s


# --- section_spans(): the extraction's equivalence pin (#167) -----------------
# `section_spans` is an EXTRACTION of validate()'s own line loop -- not a new split --
# so the test that certifies it has to compare it against that loop AS SHIPPED, never
# against itself. `_validate_line_sets_before_the_extraction` below has TWO arms with
# TWO DIFFERENT GUARANTEES, and the difference is the whole point of this comment: read
# it before trusting either arm the way the other deserves.
#
# The PROFILE and WORK arms are transcribed from `git show b831dc9:sluice/cv/validate.py`
# lines 97-123, i.e. the loop as it stood BEFORE `section_spans` existed. The first
# change made in transcription was `enumerate(..., 1)`: the pre-change loop selected
# lines without numbering them, and an equivalence assertion needs each selected line to
# carry an identity. The VALUES these two arms compare against -- the header strings
# ("PROFILE", "WORK EXPERIENCE"), the terminator set, and the WORK marker tuple -- are
# still, byte-for-byte, the pre-change code. Deriving THIS reference by reading the NEW
# helper instead would assert that the code equals itself and certify nothing -- a
# silent weakening of the fabrication gate is precisely what that would let ship green.
# What makes that argument work, for those VALUES, is that they are grounded in code
# that shipped and ran in production before `section_spans` (or SKILLS) ever existed:
# an INDEPENDENT oracle, not anyone's current belief about what the loop should do.
#
# The SKILLS arm is NOT that. #168's Task 3 extends this reference a SECOND time, by
# hand, because the alphabet the random sweep below draws from already contains
# "SKILLS", and once `section_spans` learns to pull a SKILLS bullet run out of WORK,
# this reference and `section_spans` genuinely diverge on any CV where a WORK-shaped
# bullet sits inside a SKILLS region -- measured, before this extension, on ~127/2000
# rows at the sweep's shipped seed. That divergence is EXPECTED and does not indicate a
# bug in either side: it is exactly the behaviour change #168 exists to make (a SKILLS
# bullet is no longer a WORK bullet), and the reference has to model it or the sweep
# would fail on the new, correct behaviour instead of confirming it. There is no
# `b831dc9` for SKILLS -- no prior-shipped code to transcribe, because the behaviour is
# new -- so the SKILLS branch below is typed independently from `section_spans`' own
# only in the narrow sense of "not copy-pasted": both were written by the same person,
# from the same design, close together in time. `test_the_section_span_helper_matches_
# the_pre_extraction_loop_on_random_cvs` is what exercises that independence over 2000
# rows, and it is real -- a coding slip in either side can still make them disagree --
# but it is a CONSISTENCY check between two expressions of one new idea, not an
# INDEPENDENT oracle against history. A bug both share, because both typings share the
# same misreading of the design, is exactly the failure mode this arm cannot catch.
#
# That WORK-bullet divergence is not purely a SKILLS-arm property, though, and not
# WORK-only either: it is Task 3 reaching into the PROFILE and WORK arms themselves.
# The PROFILE and WORK EXPERIENCE header handlers now also reset a new `in_skills` flag
# on entry, and a new `if in_skills: continue` gate sits between the header comparisons
# and the final profile/work appends -- code with no b831dc9 original, typed by Task 3
# for the identical reason the SKILLS branch above was. WORK is reachable through it
# because `SKILLS` deliberately does NOT clear `in_work` (see `section_spans`'s own
# comment on why): a WORK-shaped bullet under a SKILLS header mid-WORK-EXPERIENCE is
# excluded from `work` where the pre-Task-3 code would have kept it, which is the
# divergence measured above. PROFILE is reachable too, by a different route, verified
# directly rather than assumed: `PROFILE\nProse.\nSKILLS\n- Example Widget\nMore
# prose.\n` collects LESS into `profile` under this handling than the pre-Task-3 loop
# would, because entering SKILLS now clears `in_profile`, where "SKILLS" was not a
# header the pre-Task-3 loop recognised at all. So the INDEPENDENT-oracle claim above
# holds for the VALUES it names, not for "the arms behave exactly like b831dc9's loop"
# -- that second, stronger claim is false for both PROFILE and WORK once a SKILLS run
# is present, and the SKILLS-interaction control flow is exactly what all three arms
# now share: Task 3's hand-typed logic, not history.
def _validate_line_sets_before_the_extraction(cv_text):
    profile, work = [], []
    in_work = False
    in_profile = False
    in_skills = False
    for i, line in enumerate(cv_text.splitlines(), 1):
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile, in_skills = True, False
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile, in_skills = True, False, False
            continue
        if u == "SKILLS":
            in_skills, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile, in_skills = False, False, False
        # This reference never returns a `skills` list of its own -- it only needs to
        # know whether a line is CONSUMED by the SKILLS run, so it can keep it out of
        # `work` the same way `section_spans` does. The wider SKILLS-shaped marker set
        # (WORK's three plus the en and em dash) governs region CONTINUATION here, same
        # as it does in `section_spans`: a blank line does not end the run, and a
        # non-blank non-bullet line does.
        is_skills_bullet = line.lstrip().startswith(("-", "•", "*", "–", "—"))
        if in_skills and line.strip() and not is_skills_bullet:
            in_skills = False
        if in_skills:
            continue                       # a skills-region line is never a WORK bullet
        if in_profile:
            profile.append((i, line))
        if in_work and line.lstrip().startswith(("-", "•", "*")):
            work.append((i, line))
    return profile, work


def _lines_validate_profile_checks(cv_text):
    return [n for n, _ in _validate_line_sets_before_the_extraction(cv_text)[0]]


def _lines_validate_citation_checks(cv_text):
    return [n for n, _ in _validate_line_sets_before_the_extraction(cv_text)[1]]


# A CV whose PROFILE header FOLLOWS WORK EXPERIENCE: `PROFILE` sets `in_profile` without
# clearing `in_work`, so both regions are live at once and a single bullet line is
# reported by BOTH checks. Degenerate, but constructible, and it is the only input that
# can tell a line-ordered pass from an "all profile, then all work" one.
_OVERLAPPING_REGIONS_CV = (
    "PROFILE\nI did 111 things.\n\nWORK EXPERIENCE\n- bogus 999 claim [ES1]\n"
    "PROFILE\nI did 888 things.\n- second 777 bullet [ES1]\n")

# Corpus for the equivalence pin, keyed by the shape each row stands for so a failing
# row names itself: every CV shape this suite already builds, plus the
# section headers the gate does NOT terminate on (PUBLICATIONS/PROJECTS/AWARDS), the
# markers it does (`•`, `*`), header casing and indentation, both trailing-section
# orders, a CV with no headers at all, and the empty string.
_CV_FIXTURES = {
    "shipped-four-employers": _cv(FULL),
    "shipped-three-employers": _cv(FULL[:-1]),
    "shipped-work-cv": _work_cv("- Ran 42 services [ES1]"),
    "shipped-work-cv-bullet-glyph": _work_cv("• Grew team from 3 to 8"),
    "shipped-profile-cv": _cv_with_profile("I scaled platforms to 500 users."),
    "shipped-profile-cv-with-bullet":
        _cv_with_profile("I build.", "- Led 777 deployments [ES1]"),
    "profile-header-after-work-experience": _OVERLAPPING_REGIONS_CV,
    # PUBLICATIONS / PROJECTS / AWARDS are NOT terminators: their bullets are
    # citation-checked today, and a generic all-caps splitter would stop checking them.
    "publications-does-not-terminate":
        "PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
        "- did a thing [e1]\n\nPUBLICATIONS\n- a paper [e1]\n",
    "projects-and-awards-do-not-terminate":
        "PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
        "- did a thing [e1]\n\nPROJECTS\n* a project\n\nAWARDS\n• a prize\n",
    # CERTIFICATES and EDUCATION DO terminate, in either order.
    "certificates-terminates":
        "PROFILE\nProse.\n\nWORK EXPERIENCE\n- did a thing [e1]\n\nCERTIFICATES\n- a cert\n",
    "education-then-certificates-terminate":
        "PROFILE\nProse.\n\nWORK EXPERIENCE\n- did a thing [e1]\n\nEDUCATION\n- a degree\n"
        "\nCERTIFICATES\n- a cert\n",
    # Headers are compared strip()ed and upper()ed; bullets are matched lstrip()ed.
    "headers-cased-and-indented":
        "  profile  \nProse 500.\n\n  Work Experience  \n\t* starred [ES1]\n"
        "   • indented [ES1]\n  education  \n- degree\n",
    # No headers at all: neither region is ever entered, so nothing is checked.
    "no-headers-at-all": "JANE ROE\n- an uncited bullet\nSome prose 500.\n",
    "empty": "",
}

_ALL_CV_FIXTURES = [pytest.param(t, id=k) for k, t in _CV_FIXTURES.items()]


@pytest.mark.parametrize("cv_text", _ALL_CV_FIXTURES)
def test_the_section_span_helper_reproduces_validates_own_profile_and_work_line_sets(cv_text):
    # The helper must return EXACTLY the lines `validate` applies each check to. The arm
    # a naive extraction drops is the reset: `in_work` ends ONLY on CERTIFICATES /
    # EDUCATION, so a generic section splitter would silently stop citation-checking
    # bullets under PUBLICATIONS or PROJECTS -- weakening the fabrication gate while
    # scoping a style rule.
    profile, work, _skills = section_spans(cv_text)
    assert [n for n, _ in profile] == _lines_validate_profile_checks(cv_text)
    assert [n for n, _ in work] == _lines_validate_citation_checks(cv_text)
    # The checks consume the RAW line (`_CITE_RE.sub("", line)`, `line.lstrip()`), so the
    # text the helper hands back has to be the raw line too, not a stripped one.
    assert (profile, work) == _validate_line_sets_before_the_extraction(cv_text)


def test_the_section_span_helper_matches_the_pre_extraction_loop_on_random_cvs():
    # The corpus above is 14 rows I CHOSE, and a table whose cases you chose certifies
    # nothing on its own -- it can only fail on a shape someone thought to include, so it
    # is exactly the kind of pin a later edit quietly outgrows. This is the SAME
    # equivalence assertion over shapes nobody picked. Seeded, so it is deterministic and
    # offline; stdlib only. The alphabet carries every header the gate models, several it
    # does not, all three bullet markers, casing, indentation and a tab-led line.
    alphabet = [
        "PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION", "PUBLICATIONS",
        "PROJECTS", "AWARDS", "SKILLS", "  profile  ", "work experience",
        "  Education  ", "JANE ROE", "Example Systems", "", "  ",
        "- did 42 things [ES1]", "\u2022 did 500 things", "* uncited 999",
        "  - indented [EA1]", "prose with 8 and 777", "02/2023\u2013present | Loc | Role",
        "[QQ7] weird", "\tTAB LED", "ALL CAPS BODY LINE",
    ]
    rng = random.Random(20260822)
    profile_seen = work_seen = 0
    for _ in range(2000):
        cv = "\n".join(rng.choice(alphabet) for _ in range(rng.randint(0, 15)))
        profile, work, _skills = section_spans(cv)
        assert (profile, work) == _validate_line_sets_before_the_extraction(cv), cv
        profile_seen += len(profile)
        work_seen += len(work)
    # Same anti-vacuity guard the table gets: a generator that happened to emit only
    # empty CVs would satisfy the assertion above under ANY extraction, including one
    # that returns two empty lists.
    assert profile_seen > 100, profile_seen
    assert work_seen > 100, work_seen


def test_the_equivalence_corpus_actually_exercises_both_line_sets():
    # An equivalence pin over a corpus that selects nothing would hold under ANY
    # extraction, including one that returns two empty lists. Assert the corpus has real
    # content on both sides before any of the parametrised rows above are believed.
    texts = _CV_FIXTURES.values()
    profile_total = sum(len(_lines_validate_profile_checks(t)) for t in texts)
    work_total = sum(len(_lines_validate_citation_checks(t)) for t in texts)
    assert profile_total >= 10, profile_total
    assert work_total >= 10, work_total


def test_a_bullet_under_PUBLICATIONS_is_still_a_WORK_bullet():
    cv = ("PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
          "- did a thing [e1]\n\nPUBLICATIONS\n- a paper [e1]\n")
    _, work, _skills = section_spans(cv)
    assert len(work) == 2, "PUBLICATIONS does not end the WORK section"


def test_CERTIFICATES_ends_the_WORK_section():
    cv = ("PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
          "- did a thing [e1]\n\nCERTIFICATES\n- a cert\n")
    _, work, _skills = section_spans(cv)
    assert len(work) == 1, "CERTIFICATES ends the WORK section"


def test_a_bullet_under_PUBLICATIONS_is_still_citation_checked_by_validate():
    # The half that matters to the GATE rather than to the helper: the terminator set is
    # load-bearing because an uncited bullet under a section validate does not model must
    # still be refused. If a generic all-caps splitter ever replaces the explicit pair,
    # this goes red at the gate, not just at the span.
    cv = ("PROFILE\nI build.\n\nWORK EXPERIENCE\n"
          "Example Systems\n02/2023–present | Example Location A | Staff Engineer\n"
          "- Ran 42 services [ES1]\n\nPUBLICATIONS\n- An uncited paper\n")
    v = validate(cv, _bundle())
    assert any("UNCITED BULLET" in x for x in v), v


def test_validate_reports_violations_in_line_order_across_overlapping_regions():
    # `PROFILE` after `WORK EXPERIENCE` leaves BOTH regions live, so line 5's bullet
    # violation falls BETWEEN two profile violations and line 8 is reported by both
    # checks, profile first. validate() feeds this list back to the composer verbatim on
    # its single retry, so the ORDER is output, not an implementation detail: a naive
    # extraction that runs the profile pass then the work pass resequences it.
    # Measured against the pre-extraction code at b831dc9, not predicted.
    assert validate(_OVERLAPPING_REGIONS_CV, _bundle()) == [
        "INVENTED PROFILE METRIC 111 not in bundle: I did 111 things.",
        "INVENTED METRIC ['999'] not in ['ES1']: - bogus 999 claim",
        "INVENTED PROFILE METRIC 888 not in bundle: I did 888 things.",
        "INVENTED PROFILE METRIC 777 not in bundle: - second 777 bullet",
        "INVENTED METRIC ['777'] not in ['ES1']: - second 777 bullet",
    ]


def test_a_stale_text_caller_fails_loudly_without_echoing_the_bundle():
    """The second parameter changed TYPE while keeping its POSITION, so a caller left on
    the old signature must be told what to do rather than dying inside the gate with an
    AttributeError that reads as a gate bug.

    The message names the type and NO PART of the value. The stale argument is the whole
    rendered bundle -- the user's baseline CV verbatim plus every entry's company, title,
    metrics and body -- and cv/engine.py:795 logs a failed run with %s, so an
    interpolated argument writes the user's CV source corpus into a log file. Asserting
    the absence is the load-bearing half: a `{sources}` spelling contains no `repr` and
    satisfies a naive "never repr" rule while leaking identically (a NamedTuple's str()
    IS its repr()).
    """
    stale = "=== BASELINE CV ===\nJane Roe, 12 years, secret-contact-line\n"
    with pytest.raises(TypeError) as ei:
        validate("PROFILE\nI build.\n", stale)
    assert "bundle_sources" in str(ei.value)
    # The two leak assertions come BEFORE the type-name assertion, deliberately: a
    # bare `assert` raises and halts the function on its first failure, so whichever
    # assertion is EARLIEST in a failing run is the only one that has actually fired
    # -- everything after it never executes. A realistic drift on this guard is
    # someone adding the value back "for debuggability" ALONGSIDE the type name, e.g.
    # f"...not {type(sources).__name__} ({sources})", which still contains "str".
    # With the type-name check ordered first, that drift (and the plainer
    # `{sources}`-only drift this file's mutation witness targets) would halt on
    # "str" before ever reaching the leak checks below it -- proven by running the
    # witness: deleting both leak assertions changed nothing, because they were
    # unreachable dead code under that failure. Leak checks first means the ACTUAL
    # leak is what a failing run reports, and the leak checks are the ones a passing
    # run has genuinely exercised.
    assert "secret-contact-line" not in str(ei.value)
    assert "Jane Roe" not in str(ei.value)
    assert "str" in str(ei.value)
