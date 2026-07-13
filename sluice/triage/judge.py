"""Batched LLM judgment over the ambiguous (kept) leads. Each batch is one backend
call returning a JSON verdict array; parsing tolerates surrounding prose. A batch
that will not parse after one retry is skipped (recorded upstream) rather than
aborting the whole run."""
import json
import re

from sluice.core.log import get_logger
from sluice.core.dossier import slim
from sluice.triage.prompt import SYSTEM_PROMPT

_log = get_logger("triage.judge")


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
        'Each: {"lead_id":"...","verdict":"shortlist|research|dismiss",'
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
                parsed = parse_verdicts(backend.complete(prompt))
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
