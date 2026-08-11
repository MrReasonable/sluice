"""Tier 1 (free, URL-pattern) then tier 2 (a real, no-LLM page visit) company
resolution for a blank-company `needs_review` lead (#109). Both tiers abstain
rather than guess: classify.py's blank-company branch already treats a blank
company as the honest "unknown" state, and a wrong company would silently carry
through keep -> judge -> apply -> a CV addressed to the wrong employer, which is
worse than staying blank."""
import json
import re

from sluice.core.vault import frontmatter_safe

# Anchored full-string, deliberately narrow: a page_title that merely CONTAINS
# "at"/"hiring" without this exact shape must abstain, not guess a company from a
# coincidental substring match (see the near-miss test in test_triage_resolve.py).
_TITLE_PATTERNS = (
    re.compile(r"^(?P<role>.+?)\s+at\s+(?P<company>.+?)\s+\|\s+.+$"),
    re.compile(r"^(?P<company>.+?)\s+is\s+hiring\s+(?:a|an)\s+.+$"),
)


# How deep `_iter_nodes` will walk board-authored JSON-LD before abstaining. Named
# rather than inlined so the boundary test can be written AGAINST the cap instead of
# against a copied literal that would drift silently the day this number changes.
_MAX_DEPTH = 6


def _iter_nodes(data, depth: int = 0):
    """Every JSON object reachable in a JSON-LD payload, flattening arrays and `@graph`.

    Recursive rather than one-level because the capture side hands over an ARRAY of
    blocks (`core/app.py`'s `_LD_JSON_JS` collects every `ld+json` script tag, since the
    page's JobPosting is often not the first), and any ONE of those blocks may itself be
    a bare object, an array of nodes, or a `@graph` container -- so a JobPosting can sit
    two levels down. A one-level walk reads a `@graph` wrapper's own (absent) `@type` and
    abstains, silently, on a page that did publish what was asked for.

    Anything that is neither a list nor a dict yields nothing, which is what skips the
    `null` the capture writes for a block the page could not parse. Depth-capped
    (`_MAX_DEPTH`) because this is board-authored, untrusted input: without the cap a
    payload nested a few hundred levels deep raises RecursionError out of the `yield
    from` chain, and the cap's value is well past the deepest real shape
    (array -> block -> @graph -> node). A node deeper than that is not read at all, so
    tier 2 abstains on it rather than resolving it."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item, depth + 1)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, (list, dict)):
            yield from _iter_nodes(graph, depth + 1)


def _hiring_org_from_jsonld(raw: str) -> str | None:
    """schema.org/JobPosting -> hiringOrganization.name, tolerating a bare object, a
    list of nodes, or a `@graph` array -- and any malformed/missing shape, which
    abstains (None) rather than raising. That last promise is scoped to SHAPE:
    reached through `resolve_company`'s guarded entry point every field TYPE is
    covered too, but called directly (as the tests do) a non-string
    `hiringOrganization.name` still raises on its own `.strip()`."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    for node in _iter_nodes(data):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "JobPosting" not in types:
            continue
        org = node.get("hiringOrganization")
        if isinstance(org, dict):
            name = (org.get("name") or "").strip()
            if name:
                return name
    return None


def _from_dossier(dossier: dict) -> str | None:
    """Tier 2's pure extraction step: JSON-LD first (structured, board-authored,
    highest confidence), then a small set of real-capture-validated title shapes.
    JSON-LD wins when both are present and disagree. Same scoping as
    `_hiring_org_from_jsonld` above: only through `resolve_company` is a non-string
    `page_title` an abstain rather than a `TypeError` out of `re.Pattern.match`."""
    hit = _hiring_org_from_jsonld(dossier.get("structured_data") or "")
    if hit:
        return hit
    title = dossier.get("page_title") or ""
    for pattern in _TITLE_PATTERNS:
        m = pattern.match(title)
        if m:
            company = m.group("company").strip()
            if company:
                return company
    return None


def resolve_company(fm: dict, get_source, dossier_cache, *,
                    no_llm: bool, company_resolve_fetch: bool = False) -> str | None:
    """Tier 1 then tier 2, first confident match wins. Returns None -- never a guess --
    when both abstain, INCLUDING when a candidate fails `frontmatter_safe`
    (falsy, all-whitespace, unprintable, or a frontmatter-structural character; the
    all-whitespace case is reachable here specifically: wellfound.py's
    `slug.replace("-", " ").title()` returns "   " for a `/company/---` path segment,
    which is PRINTABLE and truthy, so only the guard's own `.strip()` clause catches
    it). `get_source` is `sluice.ingest.sources.get` (or None, meaning tier 1 always
    abstains), injected so this stays testable without importing the real registry."""
    url = fm.get("url") or ""
    src_id = fm.get("source") or ""
    if get_source is not None and url and src_id:
        try:
            source = get_source(src_id)
        except KeyError:
            source = None
        extractor = getattr(source, "company_from_url", None)
        if extractor:
            try:
                hit = frontmatter_safe(extractor(url))
            except Exception:
                hit = None  # a per-source extractor is newly-authored, hand-maintained regex
                            # code running against live scraped URLs -- exactly the untrusted
                            # input class frontmatter_safe exists for. One source's bug on one
                            # unanticipated URL shape must not crash the whole triage run.
            if hit:
                return hit
    if no_llm or not company_resolve_fetch or not url:
        return None
    try:
        dossier = dossier_cache.get_or_build(fm)
        return frontmatter_safe(_from_dossier(dossier))
    except Exception:
        return None  # a failed fetch just means "couldn't resolve" -- fall through to
                     # classify()'s existing needs_review branch, not a fatal per-lead error.
                     # Widened to also cover _from_dossier/frontmatter_safe: tier 2 reads live,
                     # board-authored JSON-LD and page titles with NO schema enforcement at
                     # read time -- hiringOrganization.name can be a list/dict/number/bool
                     # instead of a string (making _hiring_org_from_jsonld's own .strip()
                     # raise AttributeError), and a hand-edited or pre-#109 cache entry can
                     # carry a non-string page_title (making re.Pattern.match() raise
                     # TypeError). Both are reachable through ordinary tier-2 operation, not
                     # just a corrupted cache, so both must abstain rather than crash the
                     # whole triage batch over one bad lead -- the same reason the extractor
                     # call above gets its own except Exception.
