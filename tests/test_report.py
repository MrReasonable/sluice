from sluice.cli import _format_degraded
from sluice.ingest.engine import RunReport, SourceResult


def test_format_degraded_lists_reason_and_marks_retired():
    report = RunReport()
    report.sources = [
        SourceResult("cord", drift="zero"),
        SourceResult("wttj", drift="redirect", retired=True),
        SourceResult("bayt", status="ok"),  # healthy → omitted
    ]
    text = _format_degraded(report)
    assert "- cord: zero" in text
    assert "- wttj: redirect [RETIRED]" in text
    assert "bayt" not in text


def test_format_degraded_includes_error_source():
    report = RunReport()
    report.sources = [SourceResult("bad", status="error", error="boom")]
    assert "- bad: error" in _format_degraded(report)


def test_degraded_property_lists_drift_pairs():
    report = RunReport()
    report.sources = [SourceResult("cord", drift="zero"),
                      SourceResult("ok", status="ok")]
    assert report.degraded == [("cord", "zero")]
