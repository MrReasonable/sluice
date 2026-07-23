# tests/test_cv_validate.py
from sluice.cv.bundle import build_bundle, render_bundle
from sluice.cv.validate import validate

# Sample employer roster / decoy list a caller (CvConfig.employers /
# CvConfig.fabrication_decoys) would supply; validate() itself ships with no
# hardcoded employers or decoys, so tests exercising those gates pass them in.
EMPLOYERS = ["Example Systems", "Example Analytics", "Example Robotics",
             "Example Cartography"]
FABRICATION_DECOYS = ["Example Decoy"]

# Built through the real renderer rather than by hand. The hand-written version
# this replaces had no `=== ... ===` headers, no entry bodies, no baseline and no
# negatives -- so validate() had never once been exercised against the text
# render_bundle actually produces, which is how a defect in the contract between
# them survived the whole suite. Ids are EF1/ET1/ET2 (assign_codes sequences per
# prefix from 1); `jd_keywords=[]` and an explicit prefix_map are both required to
# make them deterministic, because build_bundle ranks before it assigns codes.
_LEGACY_ENTRIES = [
    {"company": "Example Foundry", "title": "EM", "metrics": "3 8",
     "best_for": "", "category": ""},
    {"company": "Example Telemetry", "title": "CTO", "metrics": "90 99",
     "best_for": "", "category": ""},
    {"company": "Example Telemetry", "title": "Lead", "metrics": "15",
     "best_for": "", "category": ""},
]
BUNDLE = render_bundle(build_bundle(
    entries=_LEGACY_ENTRIES, baseline="Baseline prose.",
    negatives=["never claim 400 users"], jd_keywords=[],
    prefix_map={"Example Foundry": "EF", "Example Telemetry": "ET"}))

def _cv(work):
    L = ["Phone number: +00 000", "Email address: x@y", "Web: https://x", "", "JANE ROE",
         "", "PROFILE", "I lead.", "", "WORK EXPERIENCE", ""]
    for co, dl, bs in work:
        L += [co, dl] + bs + [""]
    L += ["CERTIFICATES", "- CSM", "", "EDUCATION", "- Uni"]
    return "\n".join(L)

# Synthetic throughout. Only the descending start years matter to the gate
# (validate.py:39); the count, roles, cities and employers are arbitrary.
FULL = [
    ("Example Systems", "02/2023–present | Alfa | Staff Engineer", ["- Shipped it [EF1]"]),
    ("Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer",
     ["- Grew team from 3 to 8 [EF1]"]),
    ("Example Robotics", "09/2017–05/2020 | Charlie | Engineer", ["- Coached [EF1]"]),
    ("Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer", ["- CI [EF1]"]),
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
    f[0] = ("Example Systems", "02/2023–present | Alfa | Staff Engineer", ["- Built at Example Decoy [EF1]"])
    assert validate(_cv(f), BUNDLE) == []

def test_id_digits_not_counted_as_metric():
    # The `1` in [ET1] is part of the citation CODE, not a metric of that entry --
    # `_bundle_ids_and_nums` scans only the text AFTER the id token for that reason.
    #
    # The first assertion below is the one this test shipped with, and it was
    # INERT: a digit-free bullet leaves bullet_nums empty, so `invented` is empty
    # whatever the id token contributes, and the assertion held under every
    # mutation. It was already inert before the render_bundle port; running the
    # port's test->mutation pairs is what surfaced it. Kept (it still pins that a
    # digit-free citing bullet is clean) and paired with the load-bearing half.
    f = [x[:] for x in FULL]
    f[3] = ("Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer",
            ["- Owned direction [ET1]"])
    assert validate(_cv(f), BUNDLE) == []

    # `1` appears ONLY inside the id token [ET1]; ET1's metrics are 90 and 99. If
    # the parser scanned the id token too, the code's own digits would silently
    # become permitted figures, so this bullet MUST be flagged.
    f[3] = ("Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer",
            ["- Owned 1 direction [ET1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_multi_citation_union():
    f = [x[:] for x in FULL]
    f[3] = ("Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer",
            ["- Lifted uptime 90 to 99 across a 15-person team [ET1] [ET2]"])
    assert validate(_cv(f), BUNDLE) == []

def test_invented_metric_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer", ["- Grew team from 3 to 23 [EF1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_uncited_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer", ["- Grew team from 3 to 8"])
    assert any("UNCITED" in x for x in validate(_cv(f), BUNDLE))

def test_missing_employer_flagged():
    assert any("MISSING EMPLOYER" in x for x in
               validate(_cv(FULL[:-1]), BUNDLE, employers=EMPLOYERS))

def test_decoy_flagged():
    f = [x[:] for x in FULL]
    f[0] = ("Example Systems", "02/2023–present | Alfa | Staff Engineer", ["- Built at Example Decoy [EF1]"])
    assert any("Example Decoy" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_decoy_case_insensitive_flagged():
    # lowercase/mixed-case "example decoy" must not slip past a case-sensitive check.
    f = [x[:] for x in FULL]
    f[0] = ("Example Systems", "02/2023–present | Alfa | Staff Engineer", ["- Built at example decoy [EF1]"])
    assert any("FABRICATED" in x or "Example Decoy" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_bullet_marker_uncited_flagged():
    # cv_render_v2.py (the real renderer) treats '-', '•', and '*' all as bullet
    # markers, so a WORK bullet composed with '•' is rendered into the delivered
    # PDF exactly like a '-' bullet. Against the pre-fix code (which only detected
    # '-') this bullet was invisible to the gate -- no violation was raised, so
    # a fabricated/uncited claim would sail through. It must be caught here too.
    f = [x[:] for x in FULL]
    f[1] = ("Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer", ["• Grew team from 3 to 8"])
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
    return render_bundle(build_bundle(
        entries=_ENTRIES if entries is None else entries,
        baseline=baseline,
        negatives=[] if negatives is None else negatives,
        jd_keywords=[], prefix_map=_PREFIX_MAP))


def _work_cv(*bullets):
    return "\n".join(["JANE ROE", "", "PROFILE", "I build things.", "",
                      "WORK EXPERIENCE", "",
                      "Example Systems", "02/2023–present | Alfa | Staff Engineer",
                      *bullets, "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])


def _cv_with_profile(profile, *bullets):
    # A CV whose PROFILE line is caller-controlled, over the _ENTRIES bundle
    # (ES1 metrics=90 body "Ran 42 services…"; EA1 metrics=12 body "Owned 8
    # dashboards."). Default bullet is clean (42 is in ES1's body). One WORK entry,
    # so the reverse-chronology check sees a single start year and passes.
    return "\n".join(["JANE ROE", "", "PROFILE", profile, "",
                      "WORK EXPERIENCE", "",
                      "Example Systems", "02/2023–present | Alfa | Staff Engineer",
                      *(bullets or ["- Ran 42 services [ES1]"]), "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])


def test_negatives_block_does_not_widen_the_last_entrys_allowlist():
    # render_bundle appends the negatives AFTER the last [id]. Attributing them to
    # that entry lets a bullet cite it and carry a figure that exists ONLY in the
    # do-not-say list -- the one class of number the negatives exist to suppress.
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


def test_a_setext_underline_in_a_body_does_not_end_the_entry():
    # `======` is a markdown setext-H1 underline and is plausible in a pasted
    # entry body. It is not a section header, so it must not end the entry and
    # strand the numbers that follow it.
    e = [dict(_ENTRIES[0], body="Highlights\n======\nCut latency to 250 ms"), _ENTRIES[1]]
    assert validate(_work_cv("- Cut latency to 250 ms [ES1]"), _bundle(entries=e)) == []


def test_baseline_numbers_are_not_permitted_in_a_bullet():
    # Pins today's behaviour rather than changing it: the baseline block precedes
    # the first [id], so its numbers are attributed to no entry. Permitting them
    # is #30's design question, and this test makes that a deliberate change.
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    v = validate(_work_cv("- Led 777 deployments [ES1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_a_bracket_led_body_line_is_not_a_citable_id():
    # `body` and `baseline` are user free text spliced into the bundle verbatim.
    # An unanchored bracket match turned any bracket-led line into a citable id,
    # so a bullet could cite a YEAR and inherit whatever numbers followed it.
    e = [dict(_ENTRIES[0], body="[2019] Rebuilt the pipeline to 250 nodes"), _ENTRIES[1]]
    v = validate(_work_cv("- Ran 250 nodes [2019]"), _bundle(entries=e))
    assert any("BAD CITATION" in x for x in v), v


def test_a_bracket_led_body_lines_numbers_join_the_enclosing_entry():
    # The other half: refusing to treat it as an id must not DISCARD its numbers.
    # The line is part of the entry's body, so 250 belongs to that entry.
    e = [dict(_ENTRIES[0], body="[2019] Rebuilt the pipeline to 250 nodes"), _ENTRIES[1]]
    assert validate(_work_cv("- Ran 250 nodes [ES1]"), _bundle(entries=e)) == []


def test_an_id_shaped_bracket_in_free_text_is_still_a_citable_id():
    # CHARACTERISATION, not desired behaviour. Anchoring to the generated shape
    # NARROWS the free-text bypass; it does not close it, because a body line can
    # still happen to look like a real code. Closing it needs validate() to be
    # handed the true id list, which is a signature change (see #31's spec).
    # This test therefore has no killing mutation in this change -- by design: it
    # is expected to go RED the day someone closes the residual, which makes that
    # a visible, deliberate change rather than a silent one.
    e = [dict(_ENTRIES[0], body="[QQ7] fabricated 500 users"), _ENTRIES[1]]
    assert validate(_work_cv("- Scaled to 500 users [QQ7]"), _bundle(entries=e)) == []


def test_an_id_shaped_line_in_a_later_body_shadows_the_real_entry():
    # The sharp edge of the same residual, and worse than minting a spurious id:
    # when the free-text line looks like an EARLIER, REAL code, it OVERWRITES that
    # entry's allowlist rather than adding to it (`nums[cur] = ...` on the id line).
    # Both directions then go wrong at once -- the fabricated figure passes, AND
    # the entry's genuine metric is reported as INVENTED.
    #
    # Pre-existing: main behaves identically, so this is a documented bound and not
    # a regression from the anchor. Closing it needs validate() to be handed the
    # true id list, which is a signature change and out of scope here. Pinned so
    # the bound is MEASURED rather than assumed -- an earlier draft of this file
    # described the residual as narrower than it is.
    e = [_ENTRIES[0], dict(_ENTRIES[1], body="[ES1] fabricated 500 users")]
    b = _bundle(entries=e)
    assert validate(_work_cv("- Scaled to 500 users [ES1]"), b) == []
    assert any("INVENTED" in x for x in validate(_work_cv("- Held 90 uptime [ES1]"), b))


# --- Numeric floor on the PROFILE region (#30) --------------------------------

def test_invented_profile_metric_flagged():
    # 500 appears nowhere in the bundle -> flagged. The core new coverage.
    v = validate(_cv_with_profile("I scaled platforms to 500 users."), _bundle())
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_number_from_baseline_is_permitted():
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    # In the PROFILE, 777 (a baseline aggregate) is permitted...
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
