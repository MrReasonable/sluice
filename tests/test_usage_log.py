"""The usage log, the metering wrapper, and the summariser (#308).

Three properties here are the load-bearing ones, and each exists because getting it wrong
fails QUIETLY:

  * `meter(None, b, ...) is b` -- the shipped-off path must construct no wrapper at all.
  * A write failure warns and does not raise -- the tokens are already spent by then.
  * `hit_rate` is None, never 0.0, for a group with no measured input -- 0% reports a cache
    that is working badly, which is a different claim from one that was never observed.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from sluice.core.backends import BackendError, Completion, Usage
from sluice.core.usage import MeteredBackend, Totals, UsageLog, meter, summarize


def _at(*, days_ago=0):
    return lambda: datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)


def _u(**kw):
    return Usage(provider=kw.pop("provider", "deepseek"), model=kw.pop("model", "a-model"), **kw)


class _Fake:
    """A backend leg. Returns a Completion, or raises with optional burned usage."""

    def __init__(self, *, usage=None, unserved=(), error=None, error_usage=None):
        self.usage, self.unserved = usage, unserved
        self.error, self.error_usage = error, error_usage
        self.last_backend = "primary"

    def complete(self, prompt):
        if self.error is not None:
            raise BackendError(self.error, usage=self.error_usage)
        return Completion("text", usage=self.usage, unserved_usage=self.unserved)


def _rows(path):
    return [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]


# ------------------------------------------------------------------ meter(): the off path

def test_meter_with_no_log_returns_the_very_same_object():
    """Identity, not equality. The shipped default has no usage path, so the off path must
    construct nothing -- and asserting identity is what stops a wrapper being added later
    "harmlessly", which would put a delegating call and an attribute lookup on every LLM call
    of every install that never asked for metering."""
    b = _Fake()
    assert meter(None, b, "cv-compose") is b
    assert meter(None, b, "cv-compose", lead="x") is b


def test_meter_with_a_log_wraps(tmp_path):
    b = _Fake()
    wrapped = meter(UsageLog(str(tmp_path / "u.jsonl")), b, "cv-compose")
    assert isinstance(wrapped, MeteredBackend) and wrapped.inner is b


# ---------------------------------------------------------------- what a call records

def test_a_served_call_records_one_row_with_its_stage_and_lead(tmp_path):
    p = str(tmp_path / "u.jsonl")
    b = meter(UsageLog(p), _Fake(usage=_u(input_tokens=100, output_tokens=20,
                                          cache_read_tokens=80)),
              "cv-compose", lead="Example Co - Example Role")
    assert b.complete("p").text == "text"
    row, = _rows(p)
    assert row["stage"] == "cv-compose"
    assert row["lead"] == "Example Co - Example Role"
    assert (row["provider"], row["model"]) == ("deepseek", "a-model")
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (100, 20, 80)
    # Omitted on the common path rather than written true, so the file stays readable.
    assert "served" not in row


def test_a_stage_with_no_lead_omits_the_key_rather_than_writing_null(tmp_path):
    """The triage judge batches several dossiers into one call, so there is no single lead to
    name. A null would read as "we lost it"; an absent key says "there isn't one"."""
    p = str(tmp_path / "u.jsonl")
    meter(UsageLog(p), _Fake(usage=_u(input_tokens=5)), "triage-judge").complete("p")
    row, = _rows(p)
    assert "lead" not in row


def test_unserved_usage_from_the_completion_is_recorded_as_its_own_row(tmp_path):
    """A FallbackBackend primary that billed and then raised arrives here on the completion.
    It must not be folded into the serving leg's row -- that would attribute one leg's spend
    to another -- and it must not be dropped, which is what made it invisible before #308."""
    p = str(tmp_path / "u.jsonl")
    burned = _u(provider="openai", model="p-model", input_tokens=100)
    served = _u(input_tokens=90)
    meter(UsageLog(p), _Fake(usage=served, unserved=(burned,)), "triage-judge").complete("p")
    rows = _rows(p)
    assert len(rows) == 2
    by_provider = {r["provider"]: r for r in rows}
    assert by_provider["openai"]["served"] is False
    assert "served" not in by_provider["deepseek"]


def test_a_call_that_raised_after_spending_still_records_and_still_raises(tmp_path):
    """Both halves matter. The row is the whole point of #308's third requirement; the raise
    is the contract every caller above depends on, and a wrapper that swallowed it to get its
    bookkeeping done would turn a failed judge batch into a silently empty one."""
    p = str(tmp_path / "u.jsonl")
    b = meter(UsageLog(p), _Fake(error="truncated", error_usage=_u(input_tokens=100)),
              "cv-compose")
    with pytest.raises(BackendError, match="truncated"):
        b.complete("p")
    row, = _rows(p)
    assert (row["served"], row["input_tokens"]) == (False, 100)


def test_a_non_backend_exception_propagates_untouched_and_records_nothing(tmp_path):
    """The harness's ScriptedBackend raises AssertionError on an unrecognised prompt so a
    mis-wired call is LOUD (tests/harness/backend.py). A wrapper that caught broadly, or that
    wrote a row for it, would dull exactly that signal."""
    p = str(tmp_path / "u.jsonl")

    class _Boom:
        def complete(self, prompt):
            raise AssertionError("unrecognised prompt")

    with pytest.raises(AssertionError, match="unrecognised"):
        meter(UsageLog(p), _Boom(), "cv-audit").complete("p")
    assert not os.path.exists(p)


def test_last_backend_reads_through_the_wrapper(tmp_path):
    """Without the delegating property this is None, which `cli.py`'s triage digest renders
    as "the judge was never called" -- an outage report indistinguishable from the real
    thing, produced as a side effect of turning metering on."""
    inner = _Fake(usage=_u())
    inner.last_backend = "fallback"
    assert meter(UsageLog(str(tmp_path / "u.jsonl")), inner, "triage-judge").last_backend \
        == "fallback"


def test_a_wrapper_over_a_backend_with_no_last_backend_reports_None_not_an_error(tmp_path):
    """`Sluice.backend` returns a BARE provider for `--backend primary`/`fallback`, and those
    have no `last_backend` at all -- the attribute is FallbackBackend's alone."""
    class _Bare:
        def complete(self, prompt):
            return Completion("t")

    assert meter(UsageLog(str(tmp_path / "u.jsonl")), _Bare(), "x").last_backend is None


# ------------------------------------------------------------------ a write that fails

def test_a_write_failure_warns_once_and_never_fails_the_call(tmp_path, caplog):
    """The asymmetry that justifies swallowing an OSError here: the tokens are spent and the
    answer is earned by the time this row is written, so raising would destroy real work to
    protect a measurement of it.

    Made to fail by pointing the log at a path whose PARENT is a regular file, so makedirs
    cannot create it -- a real shape (a stale file where a directory is expected), and one
    that needs no permission games to reproduce as a non-root user."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    log = UsageLog(str(blocker / "sub" / "u.jsonl"))
    b = meter(log, _Fake(usage=_u(input_tokens=1)), "cv-compose")

    assert b.complete("p").text == "text"          # the call succeeded
    assert "could not write the usage log" in caplog.text

    caplog.clear()
    b.complete("p")
    assert caplog.text == ""                        # warned once, not once per call


# ------------------------------------------------------------------ reading back

def test_read_recent_keeps_the_window_skips_junk_and_includes_an_undated_row(tmp_path):
    p = tmp_path / "u.jsonl"
    now = _at()
    old = (now() - timedelta(days=40)).isoformat()
    p.write_text(
        json.dumps({"ts": now().isoformat(), "stage": "fresh"}) + "\n"
        + json.dumps({"ts": old, "stage": "stale"}) + "\n"
        + "{not json\n"
        + "\n"
        + json.dumps(["a list, not a row"]) + "\n"
        + json.dumps({"stage": "undated"}) + "\n",
        encoding="utf-8")
    stages = [r["stage"] for r in UsageLog(str(p)).read_recent(30, clock=now)]
    # Undated is INCLUDED, deliberately: better reported than silently dropped.
    assert stages == ["fresh", "undated"]


def test_read_recent_on_a_missing_file_is_empty_and_creates_nothing(tmp_path):
    """A read must not bring the file into existence. This repo has been bitten by exactly
    that: a store that created a 0-byte file on read disarmed a relocation notice for every
    later run (`core/paths.py`)."""
    p = tmp_path / "absent.jsonl"
    assert UsageLog(str(p)).read_recent(30) == []
    assert not p.exists()


def test_a_naive_timestamp_is_still_windowed_rather_than_treated_as_undated(tmp_path):
    """Nothing sluice writes is naive -- rows are aware UTC -- but a hand-edited or
    externally-generated file may be, and comparing an aware datetime with a naive one raises
    TypeError. The comparison is date-to-date for that reason, and this row is what proves it
    rather than the comment claiming it."""
    p = tmp_path / "u.jsonl"
    now = _at()
    p.write_text(
        json.dumps({"ts": "2026-09-13T12:00:00", "stage": "naive-fresh"}) + "\n"
        + json.dumps({"ts": "2026-01-01", "stage": "naive-date-stale"}) + "\n",
        encoding="utf-8")
    assert [r["stage"] for r in UsageLog(str(p)).read_recent(30, clock=now)] \
        == ["naive-fresh"]


# ------------------------------------------------------------------ summarize()

def _row(**kw):
    row = {"stage": "triage-judge", "provider": "deepseek", "model": "a-model",
           "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 50,
           "cache_write_tokens": None}
    row.update(kw)
    return row


def test_summarize_totals_and_groups():
    s = summarize([
        _row(input_tokens=100, output_tokens=10, cache_read_tokens=50),
        _row(stage="cv-compose", input_tokens=300, output_tokens=40, cache_read_tokens=0),
    ])
    assert s.total == Totals(calls=2, input_tokens=400, output_tokens=50,
                             cache_read_tokens=50, unmeasured=0)
    assert s.total.total_tokens == 450
    assert set(s.by_stage) == {"triage-judge", "cv-compose"}
    assert s.by_stage["cv-compose"].input_tokens == 300
    assert set(s.by_model) == {"deepseek/a-model"}


def test_by_model_is_keyed_by_provider_and_model_together():
    """One model name can be served by two endpoints -- a self-hosted name behind both a
    local server and a vendor -- and a row that cannot say which is not an answer."""
    s = summarize([_row(provider="openai", model="shared-name"),
                   _row(provider="deepseek", model="shared-name")])
    assert set(s.by_model) == {"openai/shared-name", "deepseek/shared-name"}


def test_an_unmeasured_call_is_counted_separately_not_as_zeros():
    """claude-max reports no counts. Folding it in as zeros would make "we spent nothing on
    this provider" and "we cannot see what we spent" the same answer, and the second is the
    one that is true."""
    s = summarize([_row(input_tokens=100, output_tokens=10, cache_read_tokens=50),
                   _row(provider="claude-max", model="cli-model", input_tokens=None,
                        output_tokens=None, cache_read_tokens=None)])
    assert (s.total.calls, s.total.input_tokens, s.total.unmeasured) == (2, 100, 1)
    assert s.by_model["claude-max/cli-model"].unmeasured == 1


def test_hit_rate_is_None_when_nothing_was_measured_never_zero():
    """0% reports a cache that is working badly; None reports one that was never observed.
    Printing the first for the second is the quiet wrong answer this codebase engineers out."""
    s = summarize([_row(provider="claude-max", input_tokens=None, cache_read_tokens=None)])
    assert s.total.hit_rate is None
    assert summarize([_row(input_tokens=200, cache_read_tokens=50)]).total.hit_rate == 0.25


def test_hit_rate_cannot_exceed_one_on_the_anthropic_shape():
    """The end-to-end version of the normalisation argument in test_backends_usage.py: with
    `Usage.input_tokens` normalised to INCLUDE cached tokens, a well-cached Anthropic call
    reports a sane rate. Reading Anthropic's raw `input_tokens` instead (10 here) would make
    this 50.0 -- which is why the normalisation lives in the parser and not in the report."""
    s = summarize([_row(provider="anthropic", input_tokens=550, cache_read_tokens=500)])
    assert 0.0 <= s.total.hit_rate <= 1.0


def test_unserved_calls_are_counted_and_their_tokens_still_total():
    """Both halves: the tokens were billed so they belong in the total, and the count is what
    makes a leg that burns tokens on every call visible rather than free."""
    s = summarize([_row(input_tokens=100), _row(input_tokens=40, served=False)])
    assert (s.total.input_tokens, s.unserved_calls) == (140, 1)


def test_a_missing_served_key_means_served():
    """It is OMITTED on the common path, so absent must not read as unserved -- otherwise
    every ordinary row would be reported as wasted spend."""
    assert summarize([_row()]).unserved_calls == 0


@pytest.mark.parametrize("junk", ["120", True, None, {}, [1]])
def test_a_non_numeric_count_is_treated_as_unreported_rather_than_crashing(junk):
    """Rows come back off disk, where a hand edit can put anything in a field. `True` is in
    this list on purpose: bool subclasses int, so a JSON `true` would otherwise total as 1.
    The string "120" likewise must not be silently trusted as a number."""
    s = summarize([_row(input_tokens=junk)])
    assert (s.total.input_tokens, s.total.unmeasured) == (0, 1)


def test_a_row_with_no_stage_or_provider_is_grouped_as_unknown_not_dropped():
    """A hand-edited or truncated row is still evidence a call happened; dropping it would
    under-report spend, which is the failure direction that matters here."""
    s = summarize([{"input_tokens": 10}])
    assert s.by_stage["unknown"].calls == 1
    assert s.by_model["unknown/unknown"].calls == 1


def test_summarize_of_nothing_is_an_empty_summary():
    s = summarize([])
    assert (s.total.calls, s.total.hit_rate, s.by_stage, s.unserved_calls) == (0, None, {}, 0)
