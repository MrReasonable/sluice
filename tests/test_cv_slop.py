# tests/test_cv_slop.py
from sluice.cv.slop import check_text

def test_em_dash_is_error():
    errs, _ = check_text("Led the team \u2014 and shipped.")
    assert errs and errs[0][1] == "EM-DASH"

def test_double_hyphen_is_error():
    errs, _ = check_text("cost-effective -- and fast")
    assert any(l == "DOUBLE-HYPHEN-DASH" for _, l, _ in errs)

def test_en_dash_date_range_is_clean():
    errs, warns = check_text("03/2024–present | Alfa | Engineer")
    assert errs == []

def test_ai_phrase_is_advisory_warning_not_error():
    errs, warns = check_text("I spearheaded a seamless rollout.")
    assert errs == []
    assert len(warns) >= 2  # spearhead, seamless
