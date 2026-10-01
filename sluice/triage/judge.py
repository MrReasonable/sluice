"""Batched LLM judgment over the ambiguous (kept) leads. Each batch is one backend
call returning a JSON verdict array; parsing tolerates surrounding prose. A batch
that will not parse after one retry is skipped rather than aborting the whole run.
A TRANSIENT backend failure is different (#333): it arrives after `RetryingBackend` has
already retried the same backend, so it is an outage for this run, and `JudgeAborted`
stops the remaining batches rather than spending a timeout on each of them. A
non-transient one is that batch's own, and skips it like a parse failure. Either way,
this function holds no reference to the report, so `triage/engine.py` reconciles
`len(verdicts)` against the dossiers it sent and records the shortfall in
`report.failures`. Without that, a total outage returned `[]` here and read
downstream as a run with nothing to do."""
import json
import re

from sluice.core.backends import BackendError
from sluice.core.log import get_logger
from sluice.core.dossier import slim
from sluice.triage.prompt import SYSTEM_PROMPT

_log = get_logger("triage.judge")


class JudgeAborted(Exception):
    """The judge's backend is unavailable for the rest of this run (#333).

    Carries the verdicts from batches that DID complete, so they are applied rather than
    thrown away with the run, and the `BackendError` that stopped it, for the report."""

    def __init__(self, verdicts, cause):
        super().__init__(str(cause))
        self.verdicts = verdicts
        self.cause = cause


def parse_verdicts(text: str):
    """Return the first JSON array of verdicts in `text`, or None. Tries a clean
    single-line array first, then a greedy regex over the whole blob."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            try:
                v = json.loads(line)
                if isinstance(v, list):
                    return v
            except json.JSONDecodeError:
                pass
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            v = json.loads(m.group())
            return v if isinstance(v, list) else None
        except json.JSONDecodeError:
            return None
    return None


def _build_prompt(batch, system_prompt):
    parts = [system_prompt, "", "# Batch"]
    for i, d in enumerate(batch, 1):
        parts += [f"## Dossier {i} lead_id: {d.get('lead_id')}",
                  "```json", json.dumps(slim(d), ensure_ascii=False), "```", ""]
    parts.append(
        f"Output ONLY a JSON array of exactly {len(batch)} verdict objects. "
        # #300: this tail restates the enum, so it has to carry `unjudgeable` too.
        # It is the LAST thing the model reads before answering, and a tail that
        # contradicts the schema above teaches the narrower vocabulary.
        'Each: {"lead_id":"...","verdict":"shortlist|research|dismiss|unjudgeable",'
        '"relevance_score":N,"fit_reasoning":"...","concerns":[],'
        '"culture_flags":[],"recommended_next_action":"..."}'
    )
    return "\n".join(parts)


def judge(dossiers, backend, *, batch_size=5, system_prompt=SYSTEM_PROMPT):
    verdicts = []
    batches = [dossiers[i:i + batch_size] for i in range(0, len(dossiers), batch_size)]
    for n, batch in enumerate(batches, 1):
        prompt = _build_prompt(batch, system_prompt)
        parsed = None
        for attempt in (1, 2):  # one retry on parse failure
            try:
                parsed = parse_verdicts(backend.complete(prompt).text)
            except BackendError as e:
                if not e.transient:
                    # This BATCH's own failure (a truncation, a 400 on an over-long prompt):
                    # skipped like an unparseable reply, and not re-sent, since the identical
                    # prompt fails identically. Aborting on it would fail every run at the
                    # same leads for good.
                    _log.warning("batch %d rejected by the backend: %s", n, e)
                    parsed = None
                    break
                # Not retried here: the backend has already retried itself, so a second
                # round on top would double the calls an outage costs and still fail.
                _log.warning("batch %d backend unavailable: %s -- stopping the judge", n, e)
                raise JudgeAborted(verdicts, e) from e
            except Exception as e:
                _log.warning("batch %d backend error: %s", n, e)
                parsed = None
            if parsed is not None:
                break
        if parsed is None:
            _log.warning("batch %d unparseable after retry; skipping %d dossiers",
                         n, len(batch))
            continue
        verdicts.extend(parsed)
    return verdicts
