"""`job-sluice usage` (#308): the parser, the pure formatter, and the handler end to end.

The formatter's job is to not over-claim, and the two rows that matter most are the ones
where printing a NUMBER would be a lie:

  * a group whose every call reported nothing spent an UNKNOWN amount, not zero
  * a hit rate with no measured input to divide by has no value, and `0.0%` reads as a cache
    that is working badly rather than one that was never observed

Both are the same failure the whole feature exists to avoid -- a report that answers a
question it cannot actually answer -- so both are pinned here rather than left to the eye.
"""
import json

import pytest

from sluice.cli import _build_parser, format_usage, main
from sluice.core.usage import summarize


def _row(**kw):
    row = {"stage": "triage-judge", "provider": "deepseek", "model": "deepseek-v4-flash",
           "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 50,
           "cache_write_tokens": None}
    row.update(kw)
    return row


def _fmt(rows, days=30, path="/x/usage.jsonl"):
    return format_usage(summarize(rows), days=days, path=path)


# ---------------------------------------------------------------------- the parser

def test_the_parser_accepts_days_and_json_and_defaults_to_thirty():
    args = _build_parser().parse_args(["usage"])
    assert (args.group, args.days, args.json) == ("usage", 30, False)
    args = _build_parser().parse_args(["usage", "--days", "7", "--json"])
    assert (args.days, args.json) == (7, True)


# ------------------------------------------------------------------- the formatter

def test_the_report_names_the_stages_and_the_file_it_read():
    """The path is in the header because the answer depends on WHICH log was read, and the
    log relocates with XDG -- a report that does not say where its numbers came from cannot
    be checked."""
    out = _fmt([_row(), _row(stage="cv-compose", input_tokens=300)],
               days=7, path="/state/sluice/usage.jsonl")
    assert "last 7 day(s)" in out and "/state/sluice/usage.jsonl" in out
    assert "triage-judge" in out and "cv-compose" in out


def test_a_group_that_measured_nothing_prints_a_dash_not_a_zero():
    """THE row. claude-max is flat-rate and reports no counts, and its accumulator is 0
    because unmeasured rows add nothing to it -- so rendering the accumulator states that a
    provider's calls were free. Measured before the fix: `track-classify 18  0  0  0` for
    eighteen real, billed-against-quota calls."""
    blind = _row(stage="track-classify", provider="claude-max",
                 input_tokens=None, output_tokens=None, cache_read_tokens=None)
    out = _fmt([blind, blind, blind])
    line, = [ln for ln in out.splitlines() if ln.startswith("track-classify")]
    label, calls, *cells = line.split()
    # The CALL COUNT is real and must still be shown -- the calls happened, and against a
    # flat-rate quota that is the number there is.
    assert calls == "3"
    # Every count column, and the rate, is a dash. Exact equality rather than "no 0 in the
    # line": a weaker check would pass on a row that printed 0 somewhere a split happened to
    # hide, and this row exists precisely because the accumulator's 0 is plausible-looking.
    assert cells == ["-", "-", "-", "-"], f"a blind group printed a count: {line!r}"


def test_a_partially_measured_group_keeps_its_measured_sum():
    """The other arm, and the reason the dash is scoped to wholly-blind groups: a group with
    some counts has a genuine FLOOR, and hiding it would throw away the only number there
    is. The footnote is what stops the floor being read as the whole bill."""
    out = _fmt([_row(input_tokens=100), _row(input_tokens=None, output_tokens=None,
                                             cache_read_tokens=None)])
    line, = [ln for ln in out.splitlines() if ln.startswith("triage-judge")]
    assert "100" in line
    assert "reported no token counts" in out and "FLOOR" in out


def test_the_floor_footnote_is_absent_when_everything_was_measured():
    """A footnote that always prints is one a reader learns to skip, and this one carries a
    real caveat about the totals -- so it must mean something when it appears."""
    assert "FLOOR" not in _fmt([_row()])


def test_the_unserved_footnote_appears_only_when_a_call_was_billed_without_serving():
    out = _fmt([_row(input_tokens=100), _row(input_tokens=40, served=False)])
    assert "billed without serving" in out
    assert "billed without serving" not in _fmt([_row()])


def test_a_model_label_is_never_truncated():
    """The model is what a reader is comparing, so clipping it to a tidy column destroys the
    report's own subject. Measured before the fix, at a fixed 16-char label: a
    `deepseek/deepseek-v4-flash` row printed as `deepseek/deepsee`, beside
    `claude-max/claud` -- two rows whose distinguishing half was gone."""
    out = _fmt([_row(provider="deepseek", model="deepseek-v4-flash"),
                _row(provider="claude-max", model="claude-sonnet-4-5")])
    assert "deepseek/deepseek-v4-flash" in out
    assert "claude-max/claude-sonnet-4-5" in out


def test_rows_are_ordered_by_spend_so_the_expensive_stage_is_first():
    """"Which stage is expensive" is one of the four questions #308 names, and an answer
    sorted alphabetically makes the reader do the comparison the report was run to do."""
    out = _fmt([_row(stage="aaa-cheap", input_tokens=1),
                _row(stage="zzz-expensive", input_tokens=9999)])
    stage_lines = [ln.split()[0] for ln in out.splitlines()
                   if ln.startswith(("aaa-", "zzz-"))]
    assert stage_lines[:2] == ["zzz-expensive", "aaa-cheap"]


def test_the_hit_rate_is_a_percentage_of_the_normalised_input():
    out = _fmt([_row(input_tokens=200, cache_read_tokens=50)])
    assert "25.0" in out


def test_an_empty_window_says_so_and_prints_no_table_of_zeros():
    """Two empty tables and a row of zeros would be TRUE and still worse than the sentence:
    a reader scanning for a number finds one, and it is not an answer to anything they
    asked."""
    out = _fmt([])
    assert "No calls recorded in this window." in out
    assert "by stage" not in out and "hit%" not in out


def test_digit_grouping_does_not_depend_on_the_locale(monkeypatch):
    """#311 established that reading digit grouping from an assumed locale is a bug here.
    This report is greppable output, so its shape must not move with the environment."""
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert "1,234,567" in _fmt([_row(input_tokens=1234567)])


# ------------------------------------------------------------- the handler, end to end

def _seed(tmp_path, monkeypatch, rows):
    from datetime import datetime, timezone
    p = tmp_path / "usage.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    p.write_text("".join(json.dumps({"ts": now, **r}) + "\n" for r in rows),
                 encoding="utf-8")
    monkeypatch.setenv("SLUICE_USAGE", str(p))
    return p


def test_the_command_reads_the_log_and_exits_zero(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, [_row(), _row(stage="cv-compose", input_tokens=300)])
    assert main(["usage", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "triage-judge" in out and "cv-compose" in out


def test_the_command_exits_zero_with_no_log_at_all(tmp_path, monkeypatch, capsys):
    """"Nothing recorded" is a true answer to the question asked, not a failure -- and a
    fresh install running this before its first LLM call must not look broken."""
    monkeypatch.setenv("SLUICE_USAGE", str(tmp_path / "never-written.jsonl"))
    assert main(["usage"]) == 0
    assert "No calls recorded" in capsys.readouterr().out


def test_the_command_creates_nothing_when_it_reads(tmp_path, monkeypatch):
    """A read must not bring the file into existence. This repo has been bitten by exactly
    that shape: `sqlite3.connect` created a 0-byte file merely by opening one, which
    disarmed a relocation notice for every later run."""
    p = tmp_path / "never-written.jsonl"
    monkeypatch.setenv("SLUICE_USAGE", str(p))
    main(["usage"])
    assert not p.exists()


def test_the_json_output_is_parseable_and_derives_its_totals(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, [_row(input_tokens=200, output_tokens=20,
                                       cache_read_tokens=50)])
    assert main(["usage", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"]["input_tokens"] == 200
    # Derived at render time, not stored, so a consumer cannot read a total that disagrees
    # with its own parts.
    assert data["total"]["total_tokens"] == 220
    assert data["total"]["hit_rate"] == pytest.approx(0.25)
    assert data["by_stage"]["triage-judge"]["calls"] == 1
    assert data["path"].endswith("usage.jsonl")


def test_the_json_hit_rate_is_null_rather_than_zero_when_nothing_was_measured(
        tmp_path, monkeypatch, capsys):
    """The machine-readable channel has to carry the same distinction as the table, or a
    dashboard built on it plots 0% for a provider that simply does not report."""
    _seed(tmp_path, monkeypatch, [_row(provider="claude-max", input_tokens=None,
                                       output_tokens=None, cache_read_tokens=None)])
    main(["usage", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["total"]["hit_rate"] is None
    assert data["total"]["unmeasured"] == 1


def test_the_command_never_constructs_a_backend(tmp_path, monkeypatch, capsys):
    """Offline, like every command but `ingest run`/`test-source`. It reports on spend that
    has already happened, so reaching a provider would be both pointless and a cost."""
    import sluice.core.app as app_mod

    def boom(*a, **k):
        raise AssertionError("usage must not build a backend")

    monkeypatch.setattr(app_mod.Sluice, "backend", boom)
    monkeypatch.setenv("SLUICE_USAGE", str(tmp_path / "u.jsonl"))
    assert main(["usage"]) == 0
    capsys.readouterr()
