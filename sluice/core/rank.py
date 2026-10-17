# sluice/core/rank.py
"""The one JD keyword tokeniser and evidence ranker (#165, #195).

Here in core, not in cv/bundle.py where `rank` began, because core/prepbrief.py ranks with it and
core may not import a sub-app. cv/bundle.py imports `rank` from here, and cv/engine.py binds
`jd_keywords` as `_jd_keywords`. The precedents are core/jdsection.py and core/protocols.py::cv_name.
`rank`'s score and `matched_keywords` read ONE haystack builder, so the terms a brief shows are
exactly the terms that ranked it.
"""
import re

from sluice.core.stem import stem_all as _stem_all


def jd_keywords(role: str, jd) -> list:
    """Every 4+ letter ASCII word in the role and the JD, lowercased, sorted, unique."""
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


def _haystack(entry: dict) -> set:
    # best_for/category/title, never body: matching into free prose lets a long entry
    # out-score a precise one on volume alone (see `rank`).
    return _stem_all(f"{entry.get('best_for', '')} {entry.get('category', '')} "
                     f"{entry.get('title', '')}")


def rank(entries: list[dict], keywords: list[str]) -> list[dict]:
    """Order entries by how many JD keywords their classification fields answer.

    Matching is on STEMS, both sides (#165), so "documenting", "documentation" and
    "documented" all rank the same entry. Before this it was raw substring containment,
    which missed every inflection -- measured on a real posting, an entry that directly
    evidenced the ad's most-emphasised requirement scored ZERO and ranked below dozens of
    unrelated ones, because the ad said "documenting" and the entry said "documentation".
    It also related words it should not: `"java" in "javascript"` is True.

    Orders, never excludes. The FULL verified set is emitted either way, so a ranking
    change can never lose evidence -- only move it. It DOES change which `[id]` an entry
    receives, since `assign_codes` runs after this.

    The haystack stays `best_for`/`category`/`title` and deliberately excludes `body`:
    matching into free prose lets a long entry out-score a precise one on volume alone.

    BOTH sides go through `_stem_all`, which tokenises before stemming. Stemming each
    keyword WHOLE (`_stem(k)`) is not the same operation: `_stem("machine learning")` is
    `"machine learn"`, a single string that no tokenised haystack can ever contain, so a
    multi-word keyword scored ZERO -- measured, the entry that answered it ranked last of
    seven while entries matching an unrelated keyword scored 1. That is the SAME
    two-vocabularies-nobody-normalised defect this function was rewritten to fix, one
    level down. Its production callers (cv, the interview prep brief) take their keywords
    from `core/rank.py::jd_keywords`, which yields single `[a-z]{4,}` words, for which the
    two spellings are provably identical, so this is reachability-hardening rather than a
    live bug fix -- but `rank` is reachable with any keyword list, and the two sides
    agreeing BY CONSTRUCTION is the property worth having.

    The score is counted over STEMS, so two keywords sharing a stem count once.
    """
    wanted = _stem_all(" ".join(keywords))
    return sorted(entries, key=lambda e: len(wanted & _haystack(e)), reverse=True)


def matched_keywords(entry: dict, keywords: list[str]) -> list[str]:
    """The keywords whose stem lands in `entry`'s haystack, sorted: what ranked it."""
    hay = _haystack(entry)
    return sorted({k for k in keywords if _stem_all(k) & hay})
