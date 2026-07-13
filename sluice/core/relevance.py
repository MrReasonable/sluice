"""Coarse relevance gate: a cheap title filter that runs at ingest, before dedup
and long before any LLM call.

It ships with NO opinion. Which titles are worth keeping, and which are obvious
noise, is personal to the person searching, so both lists come from config
(`relevance_keep` / `relevance_drop` in sluice.yaml) exactly as the per-source
search lists do. With neither configured the gate abstains and every parsed lead
goes through, which is correct-but-expensive: the downstream triage judge then
does all the work. Configure the lists to make ingest cheap.

Role-shape screening (target/wrong shape, pay floors) is a downstream sub-app
concern and does not belong here.
"""


def is_relevant(title: str, cfg=None) -> bool:
    """True if `title` survives the configured coarse filter.

    An empty (or absent) keep list means "keep everything"; an empty drop list
    means "drop nothing". So an unconfigured gate is a pass-through, never an
    accidental filter based on somebody else's preferences.
    """
    keep = list(getattr(cfg, "relevance_keep", None) or [])
    drop = list(getattr(cfg, "relevance_drop", None) or [])
    t = (title or "").lower()
    if keep and not any(kw in t for kw in keep):
        return False
    return not any(kw in t for kw in drop)
