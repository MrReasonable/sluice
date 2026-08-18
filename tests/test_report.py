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


def test_format_degraded_names_a_withheld_count():
    # #156's circuit breaker: the notification is the whole point once a source's real
    # leads did not reach the vault, so the withheld count must be visible in the body.
    report = RunReport()
    report.sources = [SourceResult("cwjobs", drift="fallback", withheld=25)]
    assert "- cwjobs: fallback [25 withheld]" in _format_degraded(report)


def test_format_degraded_omits_withheld_when_zero():
    # Sparse, matching the `written:` line's discipline: an ordinary drifted-but-not-
    # withheld source (e.g. `drop`) must not print a stray "[0 withheld]".
    report = RunReport()
    report.sources = [SourceResult("cord", drift="drop", withheld=0)]
    text = _format_degraded(report)
    assert "- cord: drop" in text
    assert "withheld" not in text


def test_format_degraded_surfaces_a_health_pipeline_failure(tmp_path):
    # Review-found on PR #155: `drift` stays None when `_update_health` itself raised, so
    # without also checking `health_error` this source would be silently OMITTED from the
    # notify body -- indistinguishable from a genuinely healthy run that just happened to
    # withhold nothing.
    report = RunReport()
    report.sources = [SourceResult("cwjobs", withheld=2, health_error="disk full")]
    text = _format_degraded(report)
    assert "- cwjobs: health_error [2 withheld]" in text
