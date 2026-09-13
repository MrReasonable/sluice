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

    `Sluice` builds one `UsageLog` (or None, if no path resolves) and `meter(...)` wraps a
    backend with it AT THE POINT A STAGE IS HANDED ONE. Almost all of those sites are in
    `core/app.py`, because almost every backend there serves exactly one stage and the stage
    is therefore known where the backend is constructed. `cv/engine.py` is the exception: it
    spends ONE backend on three stages (compose, audit, voice), so it takes the log itself and
    wraps per call, which is also the only place a lead id is in scope.

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
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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
        """
        if not os.path.exists(self.path):
            return []
        cutoff = clock().date()
        out = []
        with open(self.path, encoding="utf-8") as f:
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

        Present so a caller that reads it off whatever `meter` handed back gets the truth.
        `triage/engine.py` and `cv/engine.py` do `getattr(backend, "last_backend", None)`,
        and without this property that reads None through a wrapper.

        `cli.py::_format_triage_digest` documents THREE legitimate reasons `report.backend` is
        null, and tells them apart by `report.sent_to_judge` rather than by the null itself --
        so a spurious null on a run that DID judge lands on the third reading, "every batch
        raised and the judge swallowed it". That is an outage report produced as a side effect
        of turning metering on, which is a worse failure than the thing it would be a side
        effect of.
        """
        return getattr(self.inner, "last_backend", None)

    @property
    def model(self):
        """Delegated for the same reason as `last_backend`: a wrapper must not make the
        thing it wraps look like it has fewer facts about itself than it does."""
        return getattr(self.inner, "model", None)

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
            raise
        if c.usage is not None:
            self._record(c.usage)
        for u in c.unserved_usage:
            self._record(u, served=False)
        return c

    def _record(self, usage, *, served: bool = True):
        self.log.record(usage, stage=self.stage, lead=self.lead, served=served)


def meter(log, backend, stage: str, *, lead=None):
    """Wrap `backend` so each call's usage is recorded under `stage`.

    Returns `backend` UNCHANGED when `log` is None, which is the shipped state of an install
    with no usage path: no wrapper is constructed, and the cost at every call site is one
    comparison. That identity is asserted (`meter(None, b, "x") is b`) so the off path cannot
    quietly grow a wrapper later.
    """
    return backend if log is None else MeteredBackend(backend, log, stage, lead)


@dataclass(frozen=True)
class Totals:
    """One group's counts. `unmeasured` is calls that reported no input count at all --
    claude-max, or an endpoint that sent no usage block -- kept as its own number rather than
    folded in as zeros, so "we spent nothing here" and "we cannot see what we spent here" stay
    different answers."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    unmeasured: int = 0

    @property
    def hit_rate(self):
        """Cached share of input tokens, or None when there is no measured input to divide by.

        None, never 0.0: a group whose every call was unmeasured has no hit rate, and
        printing 0% there would report a cache that is working badly rather than one that was
        never observed.

        This is only correct because `Usage.input_tokens` is normalised to INCLUDE the cached
        tokens for every provider -- see `core/backends.py::Usage`. Against Anthropic's raw
        `input_tokens`, which excludes them, this ratio can exceed 1.0.
        """
        if not self.input_tokens:
            return None
        return self.cache_read_tokens / self.input_tokens

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
    got = _count(row, "input_tokens")
    return Totals(
        calls=t.calls + 1,
        input_tokens=t.input_tokens + (got or 0),
        output_tokens=t.output_tokens + (_count(row, "output_tokens") or 0),
        cache_read_tokens=t.cache_read_tokens + (_count(row, "cache_read_tokens") or 0),
        unmeasured=t.unmeasured + (1 if got is None else 0),
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
