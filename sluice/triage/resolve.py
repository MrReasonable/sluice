"""Tier 1 (free, URL-pattern) then tier 2 (a real, no-LLM page visit) company
resolution for a blank-company `needs_review` lead (#109). Both tiers abstain
rather than guess: classify.py's blank-company branch already treats a blank
company as the honest "unknown" state, and a wrong company would silently carry
through keep -> judge -> apply -> a CV addressed to the wrong employer, which is
worse than staying blank."""
import json
import re

# The two PRINTABLE characters that are still structural, so `_safe`'s `str.isprintable()`
# clause cannot speak to them. Both are structural INSIDE the double-quoted scalar
# `core/vault.py`'s `_set_fm` writes (`company: "<value>"`). Measured against PyYAML
# 6.0.3, standing in for the YAML parser a note is really read with once it leaves
# sluice -- an editor's, a script's -- as opposed to sluice's own line-based `_fm_dict`,
# which sees neither:
#   `"`   closes the scalar early -- `company: "Foo"Bar"` is a ParserError.
#   `\`   opens a YAML escape sequence. `"Foo\Bar Ltd"` is a ScannerError (unknown
#         escape), and the sequences that happen to be VALID are the worse arm, not the
#         better one: `"Foo\nBar"` parses to a real newline INSIDE the value, so what a
#         human reads back is silently not what the board published.
# `\n`/`\r` deliberately do NOT appear here: `str.isprintable()` already rejects them
# along with the rest of the C0/C1 class, and listing them twice would leave two entries
# no mutation of this tuple could ever redden.
_UNSAFE_CHARS = ('"', "\\")

# Anchored full-string, deliberately narrow: a page_title that merely CONTAINS
# "at"/"hiring" without this exact shape must abstain, not guess a company from a
# coincidental substring match (see the near-miss test in test_triage_resolve.py).
_TITLE_PATTERNS = (
    re.compile(r"^(?P<role>.+?)\s+at\s+(?P<company>.+?)\s+\|\s+.+$"),
    re.compile(r"^(?P<company>.+?)\s+is\s+hiring\s+(?:a|an)\s+.+$"),
)


def _iter_nodes(data, depth: int = 0):
    """Every JSON object reachable in a JSON-LD payload, flattening arrays and `@graph`.

    Recursive rather than one-level because the capture side hands over an ARRAY of
    blocks (`core/app.py`'s `_LD_JSON_JS` collects every `ld+json` script tag, since the
    page's JobPosting is often not the first), and any ONE of those blocks may itself be
    a bare object, an array of nodes, or a `@graph` container -- so a JobPosting can sit
    two levels down. A one-level walk reads a `@graph` wrapper's own (absent) `@type` and
    abstains, silently, on a page that did publish what was asked for.

    Anything that is neither a list nor a dict yields nothing, which is what skips the
    `null` the capture writes for a block the page could not parse. Depth-capped because
    this is board-authored, untrusted input; 6 is well past the deepest real shape
    (array -> block -> @graph -> node)."""
    if depth > 6:
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
    when both abstain, INCLUDING when a candidate contains a frontmatter-structural
    character. `get_source` is `sluice.ingest.sources.get` (or None, meaning tier 1
    always abstains), injected so this stays testable without importing the real
    registry."""
    def _safe(candidate):
        # Four rejections, none of which subsumes the next:
        #  * falsy          -- both tiers' own abstain (None, or ""); also the None-guard
        #                      the two attribute calls below need.
        #  * all-whitespace -- PRINTABLE, so isprintable() waves it through, and truthy,
        #                      so the extractor's own `or None` does too. wellfound.py's
        #                      `slug.replace("-", " ").title()` returns "   " for a
        #                      `/company/---` path segment. Checked on a COPY: the written
        #                      value stays exactly what the board published.
        #  * not printable  -- False for every C0/C1 control character, U+0085 NEL, and
        #                      every Zl/Zp separator: the class that survives sluice's OWN
        #                      frontmatter parser (`_fm_dict`/`_fm_value` split on "\n"
        #                      specifically and match `(?m)`) but that a real YAML parser
        #                      either refuses outright or, for NEL, silently folds to a
        #                      space (measured: PyYAML 6.0.3). Reachable from ordinary
        #                      well-formed input -- a legal
        #                      `{"hiringOrganization":{"name":"Example\x0bCo"}}` is a
        #                      string `_hiring_org_from_jsonld` returns intact. It also
        #                      rejects non-ASCII whitespace (NBSP from an `&nbsp;`), which
        #                      is an abstain rather than a mangle -- the direction this
        #                      whole module errs in by design.
        #  * _UNSAFE_CHARS  -- printable, but structural inside the quoted scalar; see there.
        if not candidate or not candidate.strip() or not candidate.isprintable():
            return None
        return None if any(c in candidate for c in _UNSAFE_CHARS) else candidate

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
                hit = _safe(extractor(url))
            except Exception:
                hit = None  # a per-source extractor is newly-authored, hand-maintained regex
                            # code running against live scraped URLs -- exactly the untrusted
                            # input class the _safe guard exists for. One source's bug on one
                            # unanticipated URL shape must not crash the whole triage run.
            if hit:
                return hit
    if no_llm or not company_resolve_fetch or not url:
        return None
    try:
        dossier = dossier_cache.get_or_build(fm)
        return _safe(_from_dossier(dossier))
    except Exception:
        return None  # a failed fetch just means "couldn't resolve" -- fall through to
                     # classify()'s existing needs_review branch, not a fatal per-lead error.
                     # Widened to also cover _from_dossier/_safe: tier 2 reads live,
                     # board-authored JSON-LD and page titles with NO schema enforcement at
                     # read time -- hiringOrganization.name can be a list/dict/number/bool
                     # instead of a string (making _hiring_org_from_jsonld's own .strip()
                     # raise AttributeError), and a hand-edited or pre-#109 cache entry can
                     # carry a non-string page_title (making re.Pattern.match() raise
                     # TypeError). Both are reachable through ordinary tier-2 operation, not
                     # just a corrupted cache, so both must abstain rather than crash the
                     # whole triage batch over one bad lead -- the same reason the extractor
                     # call above gets its own except Exception.
