"""Per-call LLM token accounting (#308): one JSONL row per backend call, and the pure
aggregation `job-sluice usage` renders.

WHY THIS IS A SEPARATE MODULE from `core/backends.py`. The clients and the telemetry sink
are different concerns, and one of them is on the hot path of every LLM call while the other
is a reporting surface with a CLI behind it. Keeping them apart is also what lets
`MeteredBackend` be a plain decorator over the seam rather than a flag inside four provider
classes.

WHY IT MIRRORS `triage/audit.py::AuditLog` INSTEAD OF SHARING IT. Two reasons, and the first
is structural: `AuditLog` lives in a sub-app, and everything in `sluice/` sits ON `core/`, so
importing it here inverts the layering. The second is that the two do not have the same
append contract -- `UsageLog.append` swallows an OSError (see its own docstring) and the audit
log must not -- so the shared part would be the read half alone.

    THE WIRING, in one place so it does not have to be reconstructed from call sites:

    The log is OPT-IN, so `Sluice._usage_log` answers None unless `SLUICE_USAGE` or the
    `usage_jsonl` key NAMES a file -- there is no XDG fallback, because the per-lead rows carry
    the lead's slug and so name the employers a user is applying to. **None is therefore the
    shipped state**, and `meter` returning its backend unchanged is what keeps this wrapper off
    every LLM call of an install that never asked for accounting. Do not "simplify" that branch
    away. When a log IS configured, `meter(...)` wraps a backend with it AT THE POINT A STAGE IS
    HANDED ONE. Almost all of those sites are in
    `core/app.py`, because almost every backend there serves exactly one stage and the stage
    is therefore known where the backend is constructed. `cv/engine.py` is the exception: it
    spends ONE backend on three stages (compose, audit, voice), so a stage fixed where the
    backend was built would mislabel two of the three -- it takes the log itself and wraps per
    call, with the lead attached.

    Attaching the LEAD is a separate choice from where the wrap goes, and only cv makes it.
    `triage/engine.py` has a note in scope at its own `resolve_company` call, so tier-3
    resolution COULD be attributed per lead; it is wrapped once at the boundary instead because
    it is a bulk pass over many leads and the extra parameter buys little. That is a placement
    decision, not a fact about scope -- changing it means threading the log into
    `triage/engine.py` the way cv already takes it. The recorded value is the store-issued
    `slug`, never `LeadNote.ref`, which is an opaque store handle (a filesystem path for the
    vault store) and would put the user's vault location in a telemetry file.

    There is deliberately no wrap-now-label-later: a wrapper holding `stage=None` would write
    rows nobody can group, and no static check could catch the call site that forgot to
    re-label. `tests/test_usage_wiring.py` enumerates both ends -- every `.complete(` in
    `sluice/` against every stage `meter` is called with.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sluice.core.log import get_logger

_log = get_logger("core.usage")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UsageLog:
    """Append-only JSONL of what each backend call cost.

    Timestamps are AWARE UTC, not local naive, and that is a requirement rather than a
    preference: providers apply peak/off-peak pricing over a fixed zone, so "when were these
    tokens spent" -- one of the questions #308 exists to answer -- is unanswerable from a
    stamp whose zone is whatever the laptop was set to. It also means rows from a machine
    that changed zone, or a run that crossed a DST boundary, still order correctly.
    """

    def __init__(self, path: str):
        self.path = path

    def record(self, usage, *, stage: str, lead=None, served: bool = True,
               clock=_utc_now) -> None:
        """Write one row for one call.

        `lead` is OMITTED rather than written as null when there is none: a stage that
        batches leads (the triage judge sends several dossiers per call) has no single lead
        to name, and a null there reads as "we lost it" rather than "there isn't one".

        `served=False` marks spend that did not produce the answer -- a FallbackBackend
        primary that billed and then raised. The tokens count toward totals either way,
        because they were billed either way; the flag is what keeps them identifiable.
        """
        row = {"ts": clock().isoformat(), "stage": stage}
        if lead is not None:
            row["lead"] = str(lead)
        row["provider"] = usage.provider
        row["model"] = usage.model
        if not served:
            row["served"] = False
        row["input_tokens"] = usage.input_tokens
        row["output_tokens"] = usage.output_tokens
        row["cache_read_tokens"] = usage.cache_read_tokens
        row["cache_write_tokens"] = usage.cache_write_tokens
        self.append(row)

    def append(self, entry: dict) -> None:
        """Append one row, and WARN rather than raise if the write fails.

        The asymmetry is the argument: by the time a usage row is written the tokens are
        already spent and the verdict (or the composed CV) already earned, so failing the run
        because a telemetry append hit a full disk or a read-only mount destroys real work to
        protect a measurement of it. `triage/audit.py::AuditLog.append` does NOT do this,
        because an audit row is part of the record a user reads to understand a decision.

        It is NOT the only deliberately-caught write failure in the tree, and the distinction
        matters because "no silent failures" is a hard rule here. `cli.py::cmd_init` catches
        OSError around each artefact it writes too -- but it COLLECTS each failure and returns
        1, so the caller's exit code still reports it. This one does not reach the exit code at
        all: the run succeeds and a WARNING is the whole signal. That is defensible only
        because what is lost is a measurement of work rather than the work, which is exactly
        why the same swallow would be wrong in `AuditLog` or in any write to the vault.
        """
        # ONE `write` of a whole line in append mode, deliberately, and the reason is a
        # forward hazard rather than a live one. Nothing calls a backend concurrently today:
        # `triage/engine.py`'s pool runs `dossier_cache.get_or_build` only (a browser fetch),
        # and the judge is sequential -- checked, not assumed. If a future concurrent judge
        # appends from several threads, a single write is what keeps the damage to at worst a
        # torn line, which `read_recent` skips as malformed: one row lost, nothing else
        # corrupted, and no lock on the hot path of every LLM call. Building the string before
        # opening the file is part of that -- two writes could interleave where one cannot.
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            # Once per failure, not once per call: a broken path fails on every call of a
            # long run, and a warning per lead would bury everything else in the log.
            if not getattr(self, "_warned", False):
                self._warned = True
                _log.warning(
                    "could not write the usage log at %s (%s); this run's token counts are "
                    "lost but nothing else is affected", self.path, e)

    def read_recent(self, days: int, clock=_utc_now) -> list:
        """Rows stamped within the last `days`, on the UTC date.

        Malformed lines are skipped and a row with no parseable `ts` is INCLUDED, both
        matching `AuditLog.read_recent`: a hand-edited or half-written file must not make the
        command that reads it fail, and an undated row is better reported than dropped.

        READ-FAILURE TIER: **raise**, picked from the list in `docs/ARCHITECTURE.md` rather
        than copied from a neighbour. A file that exists and cannot be read must not report as
        an empty window, because "No calls recorded" is a claim the operator acts on -- the
        same wrong answer `cmd_usage` refuses a negative `--days` to avoid, and one about
        money. A MISSING file is different and returns `[]`: that is a real first-run state,
        and the read creates nothing (a store that created a 0-byte file on read is how a
        relocation notice was once disarmed for every later run -- `core/paths.py`).

        `cli.py::cmd_usage` turns the OSError into exit 1 with a message naming the file, so
        the tier costs a diagnosis rather than a traceback. This is the opposite decision from
        `append` on the same file, deliberately: a failed WRITE must not fail a run whose work
        is already done, while a failed READ is the whole of what the reader asked for.
        """
        cutoff = clock().date()
        out = []
        # `FileNotFoundError`, never an `os.path.exists` pre-check. `exists()` swallows every
        # OSError and answers False, so an unreadable PARENT directory reported as "the file
        # is not there" and the command printed "No calls recorded" -- measured, and the exact
        # wrong answer this tier exists to refuse. Catching the specific error instead
        # distinguishes absent from unreadable, and removes a check-then-open window as a side
        # effect. Opening for READ still creates nothing.
        try:
            handle = open(self.path, encoding="utf-8")
        except FileNotFoundError:
            return []
        with handle as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                try:
                    # `.date()` on either an aware or a naive parse, so the comparison is
                    # date-to-date and never mixes an aware datetime with a naive one.
                    if (cutoff - datetime.fromisoformat(entry.get("ts", "")).date()).days > days:
                        continue
                except (ValueError, TypeError):
                    pass  # undated -> include
                out.append(entry)
        return out


class MeteredBackend:
    """A backend that records what each of its calls cost, under one stage label.

    A decorator rather than a flag inside the providers: metering is a property of the CALL
    SITE (which stage, which lead), and the providers know neither.
    """

    def __init__(self, inner, log: UsageLog, stage: str, lead=None):
        self.inner = inner
        self.log = log
        self.stage = stage
        self.lead = lead

    @property
    def last_backend(self):
        """Which leg served, delegated to the wrapped backend.

        ONE consumer reads it through a wrapper: `triage/engine.py`, whose backend
        `core/app.py` wraps before handing it down. `cv/engine.py` also does
        `getattr(backend, "last_backend", None)` but reads the BARE backend -- cv receives the
        log and wraps locally -- so it is unaffected either way.

        What a missing delegation costs, read off `cli.py::_format_triage_digest` rather than
        assumed: on a run that judged, `if report.judged:` is the arm taken, and a null
        `report.backend` merely empties its `via` clause, so the digest prints "Judged N."
        without naming a leg. That is not an outage report -- the outage arm is
        `elif report.sent_to_judge:`, unreachable while `judged` is truthy -- it is the loss of
        the fallback-degradation signal: the operator can no longer tell from the digest
        whether the primary answered or the fallback did. Cheap to delegate, and silent if not.
        (Two earlier versions of this docstring claimed the outage reading. Both were written
        from the shape of the code rather than from reading the branch order.)
        """
        return getattr(self.inner, "last_backend", None)

    def complete(self, prompt: str):
        try:
            c = self.inner.complete(prompt)
        except Exception as e:
            # BackendError carries the usage of a call that billed and then failed; anything
            # else (a fake raising AssertionError, a bug) carries none. `getattr` rather than
            # an `except BackendError` branch so a non-BackendError still propagates
            # untouched -- this wrapper must change nothing about failure behaviour.
            burned = getattr(e, "usage", None)
            if burned is not None:
                self._record(burned, served=False)
            # A call can have spent on more than one leg -- FallbackBackend with both legs
            # billing and then failing. Everything here is unserved: the call raised.
            for also in getattr(e, "unserved_usage", ()):
                self._record(also, served=False)
            raise
        if c.usage is not None:
            self._record(c.usage)
        for u in c.unserved_usage:
            self._record(u, served=False)
        return c

    def _record(self, usage, *, served: bool = True):
        """Record one row, and let NOTHING out of here.

        Broader than `UsageLog.append`'s OSError swallow, deliberately, and this is the one
        place in the metering path where a bare `except Exception` is right. Two failures it
        has to absorb, the second measured:

        - a call that SUCCEEDED must not be turned into a failure by its own bookkeeping. The
          text is already earned and the tokens already spent.
        - a call that FAILED must not have its diagnostic REPLACED. Measured before this: a
          `BackendError("the real failure")` whose `unserved_usage` held a non-`Usage` raised
          `AttributeError: 'str' object has no attribute 'provider'` out of the loop below, and
          the original error was gone. Worse than losing the message -- an AttributeError does
          not satisfy `except BackendError`, so `FallbackBackend` would not have fallen back and
          every caller's error handling would have been bypassed by a telemetry bug.

        Scoped to exactly one row, and loud in the log rather than silent. It cannot hide a
        failed gate, write or transition, because it is downstream of all three and writes
        nothing but telemetry.
        """
        try:
            self.log.record(usage, stage=self.stage, lead=self.lead, served=served)
        except Exception as e:   # noqa: BLE001 -- see the docstring: nothing may escape here
            _log.warning("could not record usage for stage %s (%s); the call itself is "
                         "unaffected", self.stage, e)


def meter(log, backend, stage: str, *, lead=None):
    """Wrap `backend` so each call's usage is recorded under `stage`.

    Returns `backend` UNCHANGED when `log` is None, which is the SHIPPED state: the log is
    opt-in, so an install that has not named a file gets None from `Sluice._usage_log` and every
    stage here is a no-op wrap. It also covers a caller that simply has no log to give -- a
    sub-app function called directly, which is how most of this repo's tests reach `run_one`,
    `run_batch` and `judge`; those pass `usage=None` and must not be made to construct a
    telemetry sink to run.

    The identity is asserted (`meter(None, b, "x") is b`) so that path cannot quietly grow a
    wrapper later, which would put a delegating call and an attribute lookup on every LLM
    call of every direct caller.
    """
    return backend if log is None else MeteredBackend(backend, log, stage, lead)


@dataclass(frozen=True)
class Totals:
    """One group's counts, with PER-COLUMN coverage beside the sums.

    `unmeasured` is calls that reported NO count at all -- claude-max, or an endpoint that sent
    no usage block -- kept as its own number rather than folded in as zeros, so "we spent
    nothing here" and "we cannot see what we spent here" stay different answers. It is keyed on
    ALL THREE counts, not on the bill: a row reporting only a cache read, which
    `openai_usage` produces from a lone `prompt_cache_hit_tokens`, printed `cached 50` while
    being counted as having reported nothing -- and the footnote then explained it as claude-max
    being flat-rate, about a deepseek row.

    `partial` is a call that reported SOMETHING but not both of input and output: those
    contribute a real number to one sum and nothing to the other, so they make the totals a
    floor without being silent. `incomplete` is the pair, and is exactly "the bill is not fully
    accounted for" -- which is what the report's floor caveat is keyed on.

    Only input and output decide whether the BILL is complete. A missing CACHE count is not an
    incomplete bill: two shipped providers report no cache write at all and a local endpoint
    reports no cache at all, so keying the caveat on it would light it permanently -- and a
    permanently-lit flag teaches its reader to skip the column, which `CLAUDE.md` already says
    about health's own.

    The three `*_calls` fields count how many rows contributed to each sum, and they exist
    because one number cannot answer that question per column. Keying the whole row's
    rendering on the INPUT count alone was the first shape and was wrong in a way the report
    states out loud: a row reporting `output_tokens` and no input -- which both parsers can
    produce, since each count is independently optional -- rendered a dash over every column,
    including the output number it really had measured. A dash is a claim that nothing is
    known, so it has to be per column.
    """
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    unmeasured: int = 0
    partial: int = 0
    input_calls: int = 0
    output_calls: int = 0
    cache_calls: int = 0
    # The subset of calls that reported an input count AND a self-consistent cache count, with
    # their two sums.
    # A ratio is only defined over rows that supplied both terms, and summing the two columns
    # INDEPENDENTLY lets a row contribute to the numerator and not the denominator -- found by
    # enumerating every combination of (absent, zero, positive) over the three counts across
    # one- and two-row groups: 36 of 756 groups produced a hit rate above 1.0, the exact number
    # this whole feature exists to report correctly. The shape is not hypothetical: with
    # `prompt_cache_hit_tokens` present, `prompt_cache_miss_tokens` absent and `prompt_tokens`
    # absent, `openai_usage`'s own fallback returns a cache read and no input. The second shape
    # the enumeration found is a row whose cache read EXCEEDS its own input, which contradicts
    # the definition of `input_tokens` and so is excluded from this subset as well -- see `_add`.
    #
    # `cache_calls - paired_calls` is how many reported cache counts the rate could not use, and
    # both are in `--json`, so the gap is visible rather than silently dropped.
    paired_calls: int = 0
    paired_input_tokens: int = 0
    paired_cache_tokens: int = 0

    @property
    def incomplete(self) -> int:
        """Calls whose spend is not fully accounted for -- silent ones plus partial ones.

        This is what makes the totals a FLOOR, and it is deliberately NOT `unmeasured` alone.
        A row reporting an input count and no output count contributes a real number to one
        sum and nothing to the other, so the total under-states the bill while `unmeasured`
        stays 0 -- measured: three rows with one reporting `input_tokens=100` printed 100 as a
        total with no caveat at all.
        """
        return self.unmeasured + self.partial

    @property
    def hit_rate(self):
        """Cached share of input tokens, or None when no call reported both terms of the ratio.

        Computed over the PAIRED subset -- the calls that reported both terms -- never over the
        two column sums. Summing them independently lets a row contribute to the numerator and
        not the denominator, and then the rate exceeds 1.0: found by enumerating every
        combination of (absent, zero, positive) over the three counts across one- and two-row
        groups, where 36 of 756 groups did exactly that. See `paired_calls`.

        `paired_calls == 0` is what stops this contradicting the `cached` column, and that case
        is the ORDINARY one rather than an edge: both parsers answer `cache_read_tokens=None`
        for a call with no prompt caching, so the column correctly shows a dash -- and without
        the gate the rate beside it read `0.0`, because a None contributes 0 to a sum. "Never
        measured" and "measured at zero" in one row, which the paragraph below forbids.

        An `input_calls` term was also tried here and removed, and the contrast is the useful
        part: it could never decide, because `input_calls == 0` implies `input_tokens == 0`
        through `_add`, so both of the cases it claimed to separate already returned None.

        None, never 0.0: a group whose cache was never measured has no hit rate, and printing
        0% there would report a cache that is working badly rather than one that was never
        observed. A group whose paired inputs total zero has none either -- there is nothing to
        divide by, and 0/0 is not 0%.

        This is only correct because `Usage.input_tokens` is normalised to INCLUDE the cached
        tokens for every provider -- see `core/backends.py::Usage`. Against Anthropic's raw
        `input_tokens`, which excludes them, the per-call ratio exceeds 1.0 too; the conformance
        suite pins that half, and the paired subset here pins the aggregate.
        """
        if not self.paired_calls or not self.paired_input_tokens:
            return None
        return self.paired_cache_tokens / self.paired_input_tokens

    @property
    def total_tokens(self) -> int:
        """Derived, never stored: a recorded total is a second value free to disagree with
        its own parts."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Summary:
    total: Totals = field(default_factory=Totals)
    by_stage: dict = field(default_factory=dict)
    by_model: dict = field(default_factory=dict)
    # Calls whose tokens were billed but did not produce the answer (a primary that failed
    # after spending). Their tokens ARE in the totals above, because they were billed; this
    # is how many of them there were, so a leg quietly burning tokens is visible.
    unserved_calls: int = 0


def _add(t: Totals, row: dict) -> Totals:
    """Fold one row into a group's totals.

    Each count is tallied INDEPENDENTLY -- both its sum and how many rows reported it -- because
    a provider may report some and not others, and the report must not generalise one column's
    silence to the rest. `unmeasured` is the row that reported NONE of them, which is the only
    row the footnote is entitled to speak for.
    """
    got = {k: _count(row, k) for k in ("input_tokens", "output_tokens", "cache_read_tokens")}
    # The BILL is input + output, and `incomplete` is "the bill is not complete". What splits
    # that into `unmeasured` and `partial` is whether the row reported ANYTHING -- all three
    # counts, not just the bill's two. A cache-only row (which `openai_usage` produces from a
    # lone `prompt_cache_hit_tokens`) reported something, so it is partial, not silent: counted
    # as silent it printed `cached 50` while the footnote called it a call that reported nothing.
    bill = (got["input_tokens"] is not None, got["output_tokens"] is not None)
    reported_any = any(v is not None for v in got.values())
    # A row is a usable term in the hit rate only if it reported BOTH counts and they are
    # self-consistent. `Usage.input_tokens` is DEFINED to include the cached tokens, so a row
    # claiming more cached than total input contradicts itself -- no provider should emit one,
    # but a nonconforming endpoint or a hand-edited line can, and two such rows aggregated put
    # the reported rate above 100%. It still contributes to the column TOTALS, which report what
    # was said; it just cannot be a term in a ratio it makes incoherent.
    paired = (got["input_tokens"] is not None and got["cache_read_tokens"] is not None
              and got["cache_read_tokens"] <= got["input_tokens"])
    return Totals(
        calls=t.calls + 1,
        input_tokens=t.input_tokens + (got["input_tokens"] or 0),
        output_tokens=t.output_tokens + (got["output_tokens"] or 0),
        cache_read_tokens=t.cache_read_tokens + (got["cache_read_tokens"] or 0),
        unmeasured=t.unmeasured + (not reported_any),
        partial=t.partial + (reported_any and not all(bill)),
        input_calls=t.input_calls + (got["input_tokens"] is not None),
        output_calls=t.output_calls + (got["output_tokens"] is not None),
        cache_calls=t.cache_calls + (got["cache_read_tokens"] is not None),
        # Only when BOTH terms of the ratio came from this row -- see `Totals.paired_calls`.
        paired_calls=t.paired_calls + paired,
        paired_input_tokens=t.paired_input_tokens + (got["input_tokens"] or 0 if paired else 0),
        paired_cache_tokens=(t.paired_cache_tokens
                             + (got["cache_read_tokens"] or 0 if paired else 0)),
    )


def _count(row: dict, key: str):
    """A row's count, or None when it is absent, null, or not a whole number.

    Rows are read back off disk, where a hand edit or a half-written line can put anything in
    a field, so a bad value is treated as unreported rather than crashing the command -- the
    same posture `read_recent` takes toward a malformed line.

    Deliberately the same admissible set as `core/backends.py::_int_or_none`, which vets a
    count on the way IN: `bool` is excluded because it subclasses `int`, so a JSON `true`
    would otherwise total as 1, and a float is excluded because token counts are whole and one
    `100.0` in a hand-edited row would otherwise turn every total that touches it into a
    float. The two functions cannot be shared (one reads a provider body, the other a stored
    row) but they must not disagree about what a count is.
    """
    v = row.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def summarize(entries) -> Summary:
    """Aggregate usage rows. Pure -- no file, no clock -- so the numbers are testable without
    a fixture on disk, and the CLI is left with formatting only.

    `by_model` is keyed `provider/model`, not `model`: a self-hosted model name can be served
    by more than one endpoint, and a row that cannot say which one it was is not an answer.
    """
    total = Totals()
    by_stage: dict = {}
    by_model: dict = {}
    unserved = 0
    for row in entries:
        total = _add(total, row)
        stage = str(row.get("stage") or "unknown")
        by_stage[stage] = _add(by_stage.get(stage, Totals()), row)
        key = f"{row.get('provider') or 'unknown'}/{row.get('model') or 'unknown'}"
        by_model[key] = _add(by_model.get(key, Totals()), row)
        # `is False`, not falsy: an absent `served` key means served (it is omitted on the
        # common path), and `None` from a hand-edited row is not a claim that it was not.
        if row.get("served") is False:
            unserved += 1
    return Summary(total=total, by_stage=by_stage, by_model=by_model,
                   unserved_calls=unserved)
