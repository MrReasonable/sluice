# tests/test_cv_validate.py
from sluice.cv.validate import validate

# Sample employer roster / decoy list a caller (CvConfig.employers /
# CvConfig.fabrication_decoys) would supply; validate() itself ships with no
# hardcoded employers or decoys, so tests exercising those gates pass them in.
EMPLOYERS = ["Novacraft", "Solarflux", "Driftwave", "Coalridge Media",
             "Roxwell Fashion", "Trueverse", "Early career (various)"]
FABRICATION_DECOYS = ["Larkspur"]

BUNDLE = "\n".join([
    "[SF3] (Solarflux) Grew team from 3 to 8 | metrics=3 8",
    "[TV1] (Trueverse) uptime 90 to 99 | metrics=90 99",
    "[TV4] (Trueverse) team of 15 | metrics=15",
])

def _cv(work):
    L = ["Phone number: +44", "Email address: x@y", "Web: https://x", "", "JANE ROE",
         "", "PROFILE", "I lead.", "", "WORK EXPERIENCE", ""]
    for co, dl, bs in work:
        L += [co, dl] + bs + [""]
    L += ["CERTIFICATES", "- CSM", "", "EDUCATION", "- Uni"]
    return "\n".join(L)

FULL = [
    ("Novacraft", "12/2025–present | LONDON | Founder", ["- Shipped it [SF3]"]),
    ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["- Grew team from 3 to 8 [SF3]"]),
    ("Driftwave", "11/2022–04/2024 | LONDON | Lead", ["- Coached [SF3]"]),
    ("Coalridge Media", "09/2020–10/2022 | LONDON | Lead", ["- CI [SF3]"]),
    ("Roxwell Fashion", "04/2017–12/2019 | LONDON | EM", ["- Led [SF3]"]),
    ("Trueverse", "05/2015–03/2017 | LONDON | CTO", ["- Uptime [SF3]"]),
    ("Early career (various)", "08/2001–03/2015 | UK", ["- Various [SF3]"]),
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
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at Larkspur [SF3]"])
    assert validate(_cv(f), BUNDLE) == []

def test_id_digits_not_counted_as_metric():
    f = [x[:] for x in FULL]
    f[5] = ("Trueverse", "05/2015–03/2017 | LONDON | CTO", ["- Owned direction [TV1]"])
    assert validate(_cv(f), BUNDLE) == []

def test_multi_citation_union():
    f = [x[:] for x in FULL]
    f[5] = ("Trueverse", "05/2015–03/2017 | LONDON | CTO",
            ["- Lifted uptime 90 to 99 across a 15-person team [TV1] [TV4]"])
    assert validate(_cv(f), BUNDLE) == []

def test_invented_metric_flagged():
    f = [x[:] for x in FULL]
    f[1] = ("Solarflux", "01/2025–04/2026 | LONDON | EM", ["- Grew team from 3 to 23 [SF3]"])
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
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at Larkspur [SF3]"])
    assert any("Larkspur" in x for x in
               validate(_cv(f), BUNDLE, fabrication_decoys=FABRICATION_DECOYS))

def test_larkspur_case_insensitive_flagged():
    # lowercase/mixed-case "larkspur" must not slip past a case-sensitive check.
    f = [x[:] for x in FULL]
    f[0] = ("Novacraft", "12/2025–present | LONDON | Founder", ["- Built at larkspur [SF3]"])
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
