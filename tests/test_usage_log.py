"""The usage log, the metering wrapper, and the summariser (#308).

Three properties here are the load-bearing ones, and each exists because getting it wrong
fails QUIETLY:

  * `meter(None, b, ...) is b` -- the SHIPPED path since the log became opt-in (#308): an
    install that has not named a file gets None from `Sluice._usage_log`, so every stage is a
    no-op wrap. It also covers a caller with no log to give, which is how most tests here reach
    a sub-app function.
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

# ONE alphabet for both ends of the pipe -- see its definition for why it is not duplicated here.
from tests.test_backends_usage import COUNT_ALPHABET, NOT_A_COUNT


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
    """Identity, not equality, on the SHIPPED path: the log is opt-in (#308), so an install that
    has named no file gets None here and every metering site is a no-op wrap. Asserting identity
    is what stops a wrapper being added later "harmlessly" -- that would put a delegating call and
    an attribute lookup on every LLM call of every install that never asked for accounting."""
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


def test_a_completion_with_no_usage_writes_no_row_and_still_returns_its_text(tmp_path):
    """`Completion.usage` is `Usage | None`, and the None arm is the one nothing exercised.

    Every shipped provider returns at least an identity `Usage` (pinned by the conformance
    suite), so the guard could be deleted and nothing would redden: `_record(None)` raises an
    AttributeError that `_record` itself catches and warns about, which is not a test failure.
    The arm is still reachable -- the seam's declared return type permits it, and an out-of-tree
    backend or a test double is the caller that produces it -- and an EMPTY row is worse than no
    row, because it would count as a call in the report while naming no provider or model.

    Both halves: no row, and the text still comes back. A wrapper that treated a missing usage
    block as an error would break a backend that is working perfectly."""
    p = str(tmp_path / "u.jsonl")
    b = meter(UsageLog(p), _Fake(usage=None), "cv-compose")
    assert b.complete("p").text == "text"
    assert not os.path.exists(p), (
        "a call that reported no usage wrote a row anyway -- an empty row counts as a call in "
        "the report while identifying nothing")
    # And the log still works afterwards: the skip is per call, not a latched off-switch.
    meter(UsageLog(p), _Fake(usage=_u(input_tokens=5)), "cv-compose").complete("p")
    assert len(_rows(p)) == 1


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


def test_read_recent_windows_on_the_exact_day_boundary(tmp_path):
    """WHERE the window ends, not merely that it ends somewhere.

    The sibling row above straddles nothing -- its rows are 0 and 40 days old against `days=30`,
    so mutating the comparison to `>= days` (or `> days + 1`) leaves it green while `--days 7`
    silently means a different span. A user comparing two weeks of spend gets an answer off by a
    day with nothing said.

    The boundary is inclusive of the `days`-th day: `--days 30` covers the 30 whole days before
    today AND today, which is 31 dates. That is a choice rather than an accident -- `--days 0`
    then means today, which `cmd_usage`'s own guard documents as legal -- so it is asserted here
    rather than left to be re-derived from the subtraction."""
    p = tmp_path / "u.jsonl"
    now = _at()
    rows = [(0, "today"), (29, "inside"), (30, "on-the-edge"), (31, "past-the-edge")]
    p.write_text("".join(
        json.dumps({"ts": (now() - timedelta(days=d)).isoformat(), "stage": s}) + "\n"
        for d, s in rows), encoding="utf-8")
    assert [r["stage"] for r in UsageLog(str(p)).read_recent(30, clock=now)] \
        == ["today", "inside", "on-the-edge"]
    # And the window MOVES with the argument, so the three above are not passing on a constant.
    assert [r["stage"] for r in UsageLog(str(p)).read_recent(29, clock=now)] \
        == ["today", "inside"]
    assert [r["stage"] for r in UsageLog(str(p)).read_recent(0, clock=now)] == ["today"]


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
                             cache_read_tokens=50, unmeasured=0, partial=0,
                             input_calls=2, output_calls=2, cache_calls=2,
                             paired_calls=2, paired_input_tokens=400,
                             paired_cache_tokens=50)
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
    """claude-max reports no counts AT ALL. Folding it in as zeros would make "we spent nothing
    on this provider" and "we cannot see what we spent" the same answer, and the second is the
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


@pytest.mark.parametrize("value,expected", COUNT_ALPHABET,
                         ids=[f"{type(v).__name__}:{v!r}" for v, _ in COUNT_ALPHABET])
def test_the_two_count_vetters_agree_on_what_a_count_is(value, expected):
    """`core/backends.py::_int_or_none` vets a count arriving from a provider's JSON;
    `core/usage.py::_count` vets one arriving back off disk. Both production docstrings say the
    two must not disagree about what a count is, and nothing checked it.

    That claim is exactly the shape this repo has been bitten by: a prose assertion with no row
    that can falsify it. Widening one side -- letting `_int_or_none` take a float, say -- edits
    that side's own parametrize list along with it, and its sibling's list, being a separate hand
    copy, stays green while a 12.5 flows into a total reported as a token count. One alphabet
    driven through both is the only version of this that can fail.

    Both directions: the junk values pin that neither side trusts a non-int, and the good values
    pin that neither side has been NARROWED (a `_count` rejecting a measured 0 would report a
    flat-rate call and a zero-cache call identically)."""
    from sluice.core.backends import _int_or_none
    from sluice.core.usage import _count

    assert _int_or_none(value) == expected
    assert _count({"input_tokens": value}, "input_tokens") == expected


@pytest.mark.parametrize("junk", NOT_A_COUNT)
def test_a_non_integer_count_is_treated_as_unreported_rather_than_crashing(junk):
    """Rows come back off disk, where a hand edit can put anything in a field.

    `True`/`False` are here on purpose: bool subclasses int, so a JSON `true` would otherwise
    total as 1 and a `false` as 0, silently. `12.5` is here because token counts are whole and
    one float would turn every total that touches it into a float. The admissible set is
    deliberately the same as `core/backends.py::_int_or_none`'s -- the two vet a count at
    opposite ends of the same pipe and must not disagree about what one is, which
    `test_the_two_count_vetters_agree_on_what_a_count_is` below is what actually pins.

    `unmeasured` stays 0 here because the row's OTHER counts are fine: it is the row that
    reported nothing at all, which this one is not. Its input COVERAGE is what drops to 0."""
    s = summarize([_row(input_tokens=junk)])
    assert (s.total.input_tokens, s.total.input_calls) == (0, 0)
    assert s.total.unmeasured == 0
    assert s.total.output_calls == 1        # the output count was perfectly good


def test_coverage_is_tracked_per_count_not_inferred_from_the_input_count():
    """A provider may report some counts and not others -- each is independently optional in
    both parsers -- so the report must not generalise one column's silence to the rest.

    Keying a whole row's rendering on the input count was the first shape, and it dashed a
    real measured output number on exactly this row. `unmeasured` is reserved for the row that
    reported NOTHING, which is the only row the report's FLOOR footnote speaks for."""
    s = summarize([_row(input_tokens=None, output_tokens=30, cache_read_tokens=None)])
    assert (s.total.input_calls, s.total.output_calls, s.total.cache_calls) == (0, 1, 0)
    assert s.total.output_tokens == 30
    assert s.total.unmeasured == 0, "a row that reported an output count reported something"
    # And with no measured input there is no rate to state, even though a column WAS measured.
    assert s.total.hit_rate is None


def test_a_row_with_no_stage_or_provider_is_grouped_as_unknown_not_dropped():
    """A hand-edited or truncated row is still evidence a call happened; dropping it would
    under-report spend, which is the failure direction that matters here."""
    s = summarize([{"input_tokens": 10}])
    assert s.by_stage["unknown"].calls == 1
    assert s.by_model["unknown/unknown"].calls == 1


def test_summarize_of_nothing_is_an_empty_summary():
    s = summarize([])
    assert (s.total.calls, s.total.hit_rate, s.by_stage, s.unserved_calls) == (0, None, {}, 0)


def test_every_leg_that_billed_on_a_failed_call_gets_its_own_row(tmp_path):
    """The worst case for cost -- paid twice, served nothing -- must not be the one case the
    log under-states. `BackendError.unserved_usage` carries the extra legs and each gets its
    own row, marked unserved, so the two are attributable by provider rather than summed into
    one anonymous number."""
    p = str(tmp_path / "u.jsonl")
    primary = _u(provider="openai", model="p-model", input_tokens=100)
    fallback = _u(provider="deepseek", model="f-model", input_tokens=40)
    err = BackendError("both backends failed", usage=primary, unserved_usage=(fallback,))

    class _BothDown:
        def complete(self, prompt):
            raise err

    with pytest.raises(BackendError):
        meter(UsageLog(p), _BothDown(), "triage-judge").complete("x")
    rows = _rows(p)
    assert [(r["provider"], r["input_tokens"], r["served"]) for r in rows] == [
        ("openai", 100, False), ("deepseek", 40, False)]


def test_an_unreadable_log_raises_rather_than_reading_as_empty(tmp_path):
    """The read-failure tier is **raise** (see `read_recent`'s docstring and the list in
    docs/ARCHITECTURE.md): an empty read is rendered as "No calls recorded", which is a claim
    about money the operator acts on.

    The unreadable path is one whose PARENT is a regular file, and that shape is the whole
    point rather than a convenience. `os.path.exists` answers FALSE there (it swallows every
    OSError), so the pre-check this fix removed would have returned `[]` and reported a first
    run -- which is the measured harm. A directory AT the path does not witness it: `exists()`
    is True for one, so the old pre-check raised too and the test passed either way. It is also
    root-safe, unlike a chmod: root ignores file modes and directory permissions alike."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    path = str(blocker / "sluice_usage.jsonl")
    assert not os.path.exists(path), "exists() must answer False here or this row is vacuous"
    with pytest.raises(OSError):
        UsageLog(path).read_recent(30)


def test_a_broken_usage_row_cannot_replace_the_error_it_was_recording(tmp_path, caplog):
    """THE inversion this guard exists for, measured before the fix.

    A `BackendError` whose `unserved_usage` holds a non-`Usage` made the recording loop raise
    `AttributeError` and the original error was GONE. That is worse than losing a message: an
    AttributeError does not satisfy `except BackendError`, so `FallbackBackend` would not have
    fallen back and every caller's error handling would have been bypassed -- by telemetry.
    """
    p = str(tmp_path / "u.jsonl")

    class _Bad:
        def complete(self, prompt):
            raise BackendError("the real failure", unserved_usage=("not-a-usage",))

    with pytest.raises(BackendError, match="the real failure"):
        meter(UsageLog(p), _Bad(), "triage-judge").complete("x")
    assert "could not record usage" in caplog.text


def test_a_broken_usage_row_cannot_fail_a_successful_call(tmp_path, caplog):
    """The other arm: the text is earned and the tokens are spent by the time a row is
    written, so bookkeeping must not turn a success into a failure."""
    p = str(tmp_path / "u.jsonl")

    class _OddUsage:
        def complete(self, prompt):
            return Completion("text", usage="not-a-usage")

    assert meter(UsageLog(p), _OddUsage(), "cv-compose").complete("x").text == "text"
    assert "could not record usage" in caplog.text


def test_partial_and_unmeasured_are_counted_separately():
    """Two different ways the totals can be a floor, and the report says different things about
    them, so they cannot be one number. `incomplete` is the pair, and is what the caveat keys
    on -- keyed on `unmeasured` alone, a partially-reported call printed a bare total."""
    silent = _row(input_tokens=None, output_tokens=None, cache_read_tokens=None)
    part = _row(input_tokens=100, output_tokens=None, cache_read_tokens=None)
    s = summarize([_row(), silent, part])
    assert (s.total.unmeasured, s.total.partial, s.total.incomplete) == (1, 1, 2)
    # The fully-reported row is neither.
    assert summarize([_row()]).total.incomplete == 0


def test_a_missing_cache_count_alone_is_not_a_partial_bill():
    """Two shipped providers report no cache WRITE at all and a local endpoint reports no cache
    at all, so keying the floor caveat on cache coverage would light it permanently -- and a
    permanently-lit flag teaches its reader to skip the column. The bill is input + output."""
    s = summarize([_row(input_tokens=100, output_tokens=10, cache_read_tokens=None)])
    assert (s.total.partial, s.total.unmeasured, s.total.incomplete) == (0, 0, 0)
    assert s.total.cache_calls == 0          # still reported as uncovered, just not a floor


def test_an_uncached_call_reports_no_hit_rate_rather_than_zero_percent():
    """The ORDINARY case, not an edge: both parsers answer `cache_read_tokens=None` for a call
    with no prompt caching, so the `cached` column shows a dash -- and the rate beside it read
    `0.0`, because a None contributes 0 to the sum. One row claiming "never measured" and
    "measured at zero" at once, which `hit_rate`'s own docstring forbids.

    A REPORTED zero is different and must still read 0.0: that is a provider saying the cache
    was live and returned nothing, which is a real and actionable number."""
    uncached = _row(input_tokens=100, output_tokens=10, cache_read_tokens=None)
    assert summarize([uncached]).total.hit_rate is None

    measured_zero = _row(input_tokens=100, output_tokens=10, cache_read_tokens=0)
    assert summarize([measured_zero]).total.hit_rate == 0.0


# --------------------------------------------------- the report's arithmetic, exhaustively

def _shape_row(i, o, c):
    return {"stage": "s", "provider": "p", "model": "m",
            "input_tokens": i, "output_tokens": o, "cache_read_tokens": c}


# Every count absent, a reported zero, or one of two positives -- so a row can be internally
# consistent OR contradict itself (more cached than total input), which is where the defect was.
_COUNT_VALUES = (None, 0, 3, 7)
_ROW_SHAPES = [(i, o, c) for i in _COUNT_VALUES for o in _COUNT_VALUES for c in _COUNT_VALUES]


@pytest.mark.parametrize("first", _ROW_SHAPES, ids=[str(s) for s in _ROW_SHAPES])
def test_the_totals_hold_their_invariants_over_every_row_shape(first):
    """ENUMERATED, not sampled, because the defect this pins was not reachable by reasoning.

    `hit_rate` summed the two columns independently, so a row contributing to the numerator and
    not the denominator put the aggregate rate ABOVE 1.0 -- the exact number the whole feature
    exists to report correctly, and the one three review rounds did not reach. Found by walking
    every combination of (absent, zero, positive) across one- and two-row groups: 36 of 756
    groups produced it.

    Two shapes cause it and both are reachable. A cache read with NO input count comes straight
    out of `openai_usage`'s fallback when `prompt_cache_hit_tokens` is present and both
    `prompt_cache_miss_tokens` and `prompt_tokens` are absent. A cache read EXCEEDING its own
    input contradicts `Usage.input_tokens`' definition and takes a nonconforming endpoint or a
    hand-edited row. Both now stay out of the ratio's paired subset while still counting toward
    the column totals, which report what was said.

    Parametrized on the first row and looping the second inside, so a failure names the shape.
    """
    for second in _ROW_SHAPES:
        rows = [_shape_row(*first), _shape_row(*second)]
        t = summarize(rows).total
        why = f"first={first} second={second} -> {t}"

        assert t.calls == 2, why
        assert t.incomplete <= t.calls, why
        assert t.unmeasured + t.partial == t.incomplete, why
        assert t.paired_calls <= min(t.input_calls, t.cache_calls), why
        # THE property: a ratio's numerator can never exceed its denominator.
        assert t.paired_cache_tokens <= t.paired_input_tokens, why
        if t.hit_rate is not None:
            assert 0.0 <= t.hit_rate <= 1.0, why
        # And a rate is never stated where no cache count was reported at all.
        if t.cache_calls == 0:
            assert t.hit_rate is None, why


def test_a_cache_read_without_an_input_count_cannot_skew_the_rate():
    """The first reachable shape, named on its own so a failure says which one broke.

    `openai_usage` produces exactly this row: hit present, miss absent, prompt_tokens absent."""
    rows = [_shape_row(None, None, 7), _shape_row(7, None, 7)]
    t = summarize(rows).total
    assert t.cache_read_tokens == 14        # the COLUMN reports what was said
    assert t.paired_calls == 1             # only one row supplied both terms
    assert t.hit_rate == 1.0               # 7/7, not 14/7


def test_a_row_claiming_more_cached_than_input_is_not_a_term_in_the_rate():
    """The second shape: self-contradictory, since `input_tokens` is defined to INCLUDE the
    cached tokens. It still counts toward the columns -- that is what the provider said."""
    t = summarize([_shape_row(0, None, 7), _shape_row(7, None, 7)]).total
    assert t.cache_read_tokens == 14 and t.cache_calls == 2
    assert t.paired_calls == 1 and t.hit_rate == 1.0


def test_a_row_with_no_cache_count_stays_out_of_the_rate_denominator_too():
    """The mirror of the numerator case, and the one the exhaustive test above cannot catch:
    that test asserts the rate's BOUNDS, and an inflated denominator understates the rate
    without ever breaching them.

    Found by mutation -- dropping the pairing condition from `paired_input_tokens` alone left
    every other row green, because the shapes they use have `input_tokens=None` on the unpaired
    row, where `or 0` contributes nothing either way. This row has a real input and no cache, so
    the two readings disagree: 7/7 paired, against 7/14 if the unpaired input is counted."""
    rows = [_shape_row(7, None, None), _shape_row(7, None, 7)]
    t = summarize(rows).total
    assert t.input_tokens == 14 and t.input_calls == 2      # the COLUMN totals both rows
    assert t.paired_calls == 1 and t.paired_input_tokens == 7
    assert t.hit_rate == 1.0, "an unpaired input inflated the denominator"


def test_a_cache_only_row_is_partial_rather_than_silent():
    """`unmeasured` is keyed on ALL THREE counts, not on the bill's two.

    `openai_usage` produces this row from a lone `prompt_cache_hit_tokens`, and counting it as
    silent made the report contradict itself: the `cached` column printed 50 while the footnote
    said the call "reported none at all -- claude-max is flat-rate", about a deepseek row. It is
    still `incomplete` (the bill is not accounted for), just not silent."""
    t = summarize([_shape_row(None, None, 50)]).total
    assert (t.unmeasured, t.partial, t.incomplete) == (0, 1, 1)
    assert t.cache_read_tokens == 50


def test_a_row_reporting_nothing_at_all_is_the_only_silent_one():
    t = summarize([_shape_row(None, None, None)]).total
    assert (t.unmeasured, t.partial, t.incomplete) == (1, 0, 1)
    assert (t.input_calls, t.output_calls, t.cache_calls) == (0, 0, 0)
