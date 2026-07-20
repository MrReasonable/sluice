# tests/test_cv_validate.py
from sluice.cv.bundle import build_bundle, render_bundle
from sluice.cv.validate import validate

# Sample employer roster / decoy list a caller (CvConfig.employers /
# CvConfig.fabrication_decoys) would supply; validate() itself ships with no
# hardcoded employers or decoys, so tests exercising those gates pass them in.
EMPLOYERS = ["Novacraft", "Solarflux", "Driftwave", "Coalridge Media",
             "Roxwell Fashion", "Trueverse", "Early career (various)"]
FABRICATION_DECOYS = ["Larkspur"]

# Built through the real renderer rather than by hand. The hand-written version
# this replaces had no `=== ... ===` headers, no entry bodies, no baseline and no
# negatives -- so validate() had never once been exercised against the text
# render_bundle actually produces, which is how a defect in the contract between
# them survived the whole suite. Ids are SF1/TV1/TV2 (assign_codes sequences per
# prefix from 1); `jd_keywords=[]` and an explicit prefix_map are both required to
# make them deterministic, because build_bundle ranks before it assigns codes.
_LEGACY_ENTRIES = [
    {"company": "Solarflux", "title": "EM", "metrics": "3 8", "best_for": "", "category": ""},
    {"company": "Trueverse", "title": "CTO", "metrics": "90 99", "best_for": "", "category": ""},
    {"company": "Trueverse", "title": "Lead", "metrics": "15", "best_for": "", "category": ""},
]
BUNDLE = render_bundle(build_bundle(
    entries=_LEGACY_ENTRIES, baseline="Baseline prose.",
    negatives=["never claim 400 users"], jd_keywords=[],
    prefix_map={"Solarflux": "SF", "Trueverse": "TV"}))

def _cv(work):
    L = ["Phone number: +44", "Email address: x@y", "Web: https://x", "", "JANE ROE",
         "", "PROFILE", "I lead.", "", "WORK EXPERIENCE", ""]
    for co, dl, bs in work:
        L += [co, dl] + bs + [""]
    L += ["CERTIFICATES", "- CSM", "", "EDUCATION", "- Uni"]
    return "\n".join(L)

FULL = [
    ("Novacraft", "12/2025–present | LONDON | Founder", ["- Shipped it [SF1]"]),
    ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["- Grew team from 3 to 8 [SF1]"]),
    ("Driftwave", "11/2022–04/2024 | LONDON | Lead", ["- Coached [SF1]"]),
    ("Coalridge Media", "09/2020–10/2022 | LONDON | Lead", ["- CI [SF1]"]),
    ("Roxwell Fashion", "04/2017–12/2019 | LONDON | EM", ["- Led [SF1]"]),
    ("Trueverse", "05/2015–03/2017 | LONDON | CTO", ["- Uptime [SF1]"]),
    ("Early career (various)", "08/2001–03/2015 | UK", ["- Various [SF1]"]),
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
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at Larkspur [SF1]"])
    assert validate(_cv(f), BUNDLE) == []

def test_id_digits_not_counted_as_metric():
    # The `1` in [TV1] is part of the citation CODE, not a metric of that entry --
    # `_bundle_ids_and_nums` scans only the text AFTER the id token for that reason.
    #
    # The first assertion below is the one this test shipped with, and it was
    # INERT: a digit-free bullet leaves bullet_nums empty, so `invented` is empty
    # whatever the id token contributes, and the assertion held under every
    # mutation. It was already inert before the render_bundle port; running the
    # port's test->mutation pairs is what surfaced it. Kept (it still pins that a
    # digit-free citing bullet is clean) and paired with the load-bearing half.
    f = [x[:] for x in FULL]
    f[5] = ("Trueverse", "05/2015–03/2017 | LONDON | CTO", ["- Owned direction [TV1]"])
    assert validate(_cv(f), BUNDLE) == []

    # `1` appears ONLY inside the id token [TV1]; TV1's metrics are 90 and 99. If
    # the parser scanned the id token too, the code's own digits would silently
    # become permitted figures, so this bullet MUST be flagged.
    f[5] = ("Trueverse", "05/2015–03/2017 | LONDON | CTO", ["- Owned 1 direction [TV1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_multi_citation_union():
    f = [x[:] for x in FULL]
    f[5] = ("Trueverse", "05/2015–03/2017 | LONDON | CTO",
            ["- Lifted uptime 90 to 99 across a 15-person team [TV1] [TV2]"])
    assert validate(_cv(f), BUNDLE) == []

def test_invented_metric_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["- Grew team from 3 to 23 [SF1]"])
    assert any("INVENTED" in x for x in validate(_cv(f), BUNDLE))

def test_uncited_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["- Grew team from 3 to 8"])
    assert any("UNCITED" in x for x in validate(_cv(f), BUNDLE))

def test_missing_employer_flagged():
    assert any("MISSING EMPLOYER" in x for x in
               validate(_cv(FULL[:-1]), BUNDLE, employers=EMPLOYERS))

def test_larkspur_flagged():
    f = [x[:] for x in FULL]
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at Larkspur [SF1]"])
    assert any("Larkspur" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_larkspur_case_insensitive_flagged():
    # lowercase/mixed-case "larkspur" must not slip past a case-sensitive check.
    f = [x[:] for x in FULL]
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at larkspur [SF1]"])
    assert any("FABRICATED" in x or "Larkspur" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_bullet_marker_uncited_flagged():
    # cv_render_v2.py (the real renderer) treats '-', '•', and '*' all as bullet
    # markers, so a WORK bullet composed with '•' is rendered into the delivered
    # PDF exactly like a '-' bullet. Against the pre-fix code (which only detected
    # '-') this bullet was invisible to the gate -- no violation was raised, so
    # a fabricated/uncited claim would sail through. It must be caught here too.
    f = [x[:] for x in FULL]
    f[1] = ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["• Grew team from 3 to 8"])
    assert any("UNCITED" in x for x in validate(_cv(f), BUNDLE))


# --- Fixtures for the section-boundary and id-anchor guards (#31) -------------
# BUNDLE above covers the legacy assertions; these add the regions it has no
# reason to exercise -- entry bodies, a baseline block, and negatives carrying
# digits -- because those are where the two defects #31 fixes actually lived.

_PREFIX_MAP = {"Acme Systems": "AC", "Borealis Data": "BO"}

_ENTRIES = [
    {"company": "Acme Systems", "title": "Engineer", "metrics": "90",
     "body": "Ran 42 services in the platform group.", "best_for": "", "category": ""},
    {"company": "Borealis Data", "title": "Analyst", "metrics": "12",
     "body": "Owned 8 dashboards.", "best_for": "", "category": ""},
]


def _bundle(entries=None, baseline="Baseline prose, no digits.", negatives=None):
    # jd_keywords=[] AND an explicit prefix_map are BOTH required for stable ids:
    # build_bundle ranks before assign_codes, so ranking decides the numbering.
    # With no keywords every entry scores 0 and the (stable) sort preserves order,
    # giving AC1 then BO1 -- so BO1 is the last-ranked entry the negatives block
    # would otherwise be attributed to.
    return render_bundle(build_bundle(
        entries=_ENTRIES if entries is None else entries,
        baseline=baseline,
        negatives=[] if negatives is None else negatives,
        jd_keywords=[], prefix_map=_PREFIX_MAP))


def _work_cv(*bullets):
    return "\n".join(["JANE ROE", "", "PROFILE", "I build things.", "",
                      "WORK EXPERIENCE", "",
                      "Acme Systems", "03/2024–present | Alfa | Engineer",
                      *bullets, "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])


def test_negatives_block_does_not_widen_the_last_entrys_allowlist():
    # render_bundle appends the negatives AFTER the last [id]. Attributing them to
    # that entry lets a bullet cite it and carry a figure that exists ONLY in the
    # do-not-say list -- the one class of number the negatives exist to suppress.
    b = _bundle(negatives=["never claim 500 users"])
    v = validate(_work_cv("- Scaled the platform to 500 users [BO1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_a_body_sourced_number_stays_permitted():
    # Guards the opposite failure: a fix that narrows the allowlist to nothing.
    # The number MUST come from the entry's body, not its metrics= line -- metrics
    # is parsed on the same line that sets `cur`, so a metrics-sourced number is
    # unreachable by any cur-clearing change and cannot detect an over-broad one.
    b = _bundle(negatives=["never claim 500 users"])
    assert validate(_work_cv("- Ran 42 services [AC1]"), b) == []


def test_a_setext_underline_in_a_body_does_not_end_the_entry():
    # `======` is a markdown setext-H1 underline and is plausible in a pasted
    # entry body. It is not a section header, so it must not end the entry and
    # strand the numbers that follow it.
    e = [dict(_ENTRIES[0], body="Highlights\n======\nCut latency to 250 ms"), _ENTRIES[1]]
    assert validate(_work_cv("- Cut latency to 250 ms [AC1]"), _bundle(entries=e)) == []


def test_baseline_numbers_are_not_permitted_in_a_bullet():
    # Pins today's behaviour rather than changing it: the baseline block precedes
    # the first [id], so its numbers are attributed to no entry. Permitting them
    # is #30's design question, and this test makes that a deliberate change.
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    v = validate(_work_cv("- Led 777 deployments [AC1]"), b)
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
    assert validate(_work_cv("- Ran 250 nodes [AC1]"), _bundle(entries=e)) == []


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
