"""Per-lead dossier cache: what the judge needs (JD markdown + Glassdoor rating +
lead snapshot), cached on disk with a TTL so re-runs skip network I/O. The fetcher
(Camofox-backed in production) and clock are injected, so TTL and hit/miss are unit
tested offline. Schema mirrors the legacy schema_version 2 so the existing cached
dossiers are reused as-is."""
import hashlib
import html
import json
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

from sluice.core.roletype import observe_role_type


# The JD-length distribution `job-sluice doctor` reports (#169), as (bucket, exclusive
# upper bound) pairs. ONE home for the boundary and its label: they were a numeric
# comparison in core/app.py and a hand-written English label in core/doctor.py, so moving
# a boundary left the label asserting the old number with the whole suite green -- and the
# end-to-end test's fixtures were chosen well clear of both, so nothing would have caught
# it. `classify_dossier_cache` renders its text from this tuple.
#
# CUMULATIVE by construction: each bound is an upper bound, so `empty` <= `under_200` <=
# `under_800`, and each bucket answers "how many are AT MOST this short" without
# subtracting the others. They therefore do NOT partition `total` -- a dossier at or above
# the largest bound is in none of them, and `unreadable` sits outside the chain entirely.
#
# 200/800 are a PRESENTATION choice, round numbers a human can eyeball. They are not a
# second opinion about which jobs are good stacked on top of `min_jd_chars`: this row
# changes nothing about which leads get judged.
JD_LENGTH_BUCKETS = (("empty", 1), ("under_200", 200), ("under_800", 800))


def _slug(lead: dict) -> str:
    base = f"{lead.get('company','')}-{lead.get('role','')}".lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:80] or "lead"


def jd_text(dossier: dict) -> str:
    """The JD markdown a cached dossier carries, stripped -- "" when it has none.

    The ONE extraction of that field. `jd_arrived` judges it and `census` measures it,
    and both used to re-derive the same three checks (is it a dict, is `markdown` a str,
    strip it) in different files. Degrades to "" rather than raising on any malformed
    shape, matching what `triage/resolve.py:_text` already does with this same field: a
    dossier that cannot answer the question has not produced a JD either. A file missing
    the `jd` key entirely is a real shape -- it predates #169, or `get_or_build`'s
    non-atomic write was interrupted mid-dump.
    """
    jd = dossier.get("jd") if isinstance(dossier, dict) else None
    markdown = jd.get("markdown") if isinstance(jd, dict) else None
    return markdown.strip() if isinstance(markdown, str) else ""


def slim(dossier: dict, *, jd_limit: int = 4000) -> dict:
    # page_title/structured_data (#109) are resolution-only fields, never judge-relevant --
    # structured_data especially can run several KB on some boards, so excluding it here
    # (not at storage time in get_or_build, which resolve.py's _from_dossier still needs
    # to read directly off the cached dict) is what keeps it out of every judge prompt.
    out = {k: v for k, v in dossier.items()
          if k not in ("lead_snapshot", "page_title", "structured_data")}
    jd = dict(out.get("jd") or {})
    if "markdown" in jd:
        jd["markdown"] = jd["markdown"][:jd_limit]
    out["jd"] = jd
    return out


class DossierCache:
    def __init__(self, dir: str, ttl_days: int, fetcher, clock=datetime.now,
                 min_jd_chars: int = 0):
        self.dir = dir
        self.ttl_days = ttl_days
        self.fetcher = fetcher
        self.clock = clock
        # 0 = the near-empty band is OFF, which is the SHIPPED default (#169, spec
        # decision 3): a character count is a judgement about what counts as a real
        # posting, and this repo does not ship one uninvited -- see sluice.yaml.example's
        # `lead_ttl_days` for the same rule stated at length. An EMPTY jd is different in
        # kind: it is a fact, so `jd_arrived` refuses it at every floor including 0.
        # The constructor default is 0 rather than the config default so the bare
        # `DossierCache(dir, ttl, fetcher=...)` constructions across the suite keep
        # today's behaviour exactly.
        self.min_jd_chars = min_jd_chars

    def jd_arrived(self, dossier: dict) -> bool:
        """Did this fetch actually produce a job description?

        The ONE owner of that judgement. `get_or_build` asks it to decide whether to
        PERSIST, and every caller asks it to decide what a miss means for them -- one
        function, two uses, so there is no second copy of the rule to drift (#169).

        A predicate rather than a marker key in the returned dict, deliberately: a marker
        would ride `slim()` into the judge prompt, since `slim` excludes `lead_snapshot`,
        `page_title` and `structured_data` by NAME and would not exclude a new key by
        accident.

        Degrades to False rather than raising on a malformed `jd`, matching what
        `triage/resolve.py:_text` already does with this same field -- a dossier that
        cannot answer the question has not produced a JD either.
        """
        text = jd_text(dossier)
        if not text:
            return False            # a FACT, refused at every floor
        return len(text) >= self.min_jd_chars

    def census(self) -> dict:
        """Bucket counts over every cached dossier on disk, for `job-sluice doctor`.

        Lives HERE because it reads this class's own on-disk layout -- the `.json`
        suffix and the directory `_path` writes into. `Sluice.doctor` had that shape
        inlined, so a change to the naming scheme would have left the scan silently
        counting ZERO and reporting "no cached dossiers yet", which reads as a fresh
        install rather than as a broken scan. It also re-derived the `jd.markdown`
        extraction that `jd_arrived` calls itself the sole owner of; both now go
        through `jd_text`.

        Pure-ish: it READS, and must never create the directory it is reporting on
        (`os.listdir` on a missing dir raises rather than creating, and the caller
        turns that into an empty census). `doctor` diagnoses a broken install, so
        every per-entry failure is counted rather than raised.

        Buckets are CUMULATIVE (`empty` <= `under_200` <= `under_800`), so each answers
        "how many are AT MOST this short" without subtracting the others. `unreadable`
        sits outside that chain -- its length is unknown, not zero -- which is why the
        buckets do not partition `total`.
        """
        counts = {"total": 0, "unreadable": 0}
        for label, _bound in JD_LENGTH_BUCKETS:
            counts[label] = 0
        try:
            entries = [e for e in os.listdir(self.dir) if e.endswith(".json")]
        except OSError:
            # Every reason the directory cannot be listed, not just "missing". A
            # mode-000 cache dir raises PermissionError, and `cli.main` converts only
            # ValueError -- so this escaping killed the one command that exists to
            # explain a broken install.
            return counts
        for entry in entries:
            counts["total"] += 1
            try:
                with open(os.path.join(self.dir, entry), encoding="utf-8") as f:
                    dossier = json.load(f)
            except (OSError, ValueError):
                # The FILE is broken -- invalid JSON, or unreadable outright (an
                # interrupted write, a bad disk). A different fault from a dossier that
                # parsed fine and carries no JD, and excluded from the length buckets
                # because its length is genuinely unknown rather than zero.
                counts["unreadable"] += 1
                continue
            length = len(jd_text(dossier))
            for label, bound in JD_LENGTH_BUCKETS:
                if length < bound:
                    counts[label] += 1
        return counts

    def cache_key(self, lead: dict) -> str:
        """`lead_id` first, then a stable hash of `url` (#109) -- url does not change
        across the classify-pass company mutation this feature performs, so the
        classify-pass resolution fetch and the later enrich-pass judge fetch land on
        the SAME cache entry instead of double-fetching. Falls back to the
        company/role slug only when neither is available (e.g. a #23 Google lead
        with no url)."""
        lead_id = lead.get("lead_id")
        if lead_id:
            return lead_id
        url = lead.get("url")
        if url:
            return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]
        return _slug(lead)

    def _path(self, lead: dict) -> str:
        return os.path.join(self.dir, f"{self.cache_key(lead)}.json")

    def _fresh(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            cached = json.loads(open(path, encoding="utf-8").read())
            now = self.clock()
            built = datetime.fromisoformat(cached.get("built_at"))
            # Reconcile the two before subtracting (#229). The production clock is
            # `datetime.now`, which is NAIVE local time, while a `built_at` written by
            # anything else -- a hand-repaired entry, or any writer reaching for the
            # `datetime.now(timezone.utc)` spelling -- carries an offset. Subtracting
            # across that boundary raises TypeError, which the handler below caught
            # alongside its genuine corrupt-file cases and reported as "not fresh".
            # That verdict was PERMANENT and silent: `get_or_build` only persists a
            # refetch that produced a JD, so nothing ever rewrote the entry and a good
            # file was re-read and re-rejected every run while looking healthy on disk.
            #
            # `astimezone()` on a NAIVE datetime reads it as local, which is exactly the
            # meaning `datetime.now` already gives it -- so no verdict changes for the
            # naive/naive pair that is today's only production shape. TypeError stays in
            # the handler below: it is still what catches a `built_at` key that is
            # missing entirely, which IS a corrupt file.
            if (built.tzinfo is None) != (now.tzinfo is None):
                built = (built.astimezone().replace(tzinfo=None) if now.tzinfo is None
                         else built.astimezone())
            age = now - built
        except (OSError, ValueError, TypeError):
            return False
        if age.days >= self.ttl_days:
            return False
        # Content as well as age (#169). An entry written BEFORE this existed whose JD
        # never arrived is fresh by the clock and useless by content; without this check
        # the fix reaches an existing deployment's cache only after a full TTL, and the
        # issue's manual "delete the sub-200-character entries" step stays manual.
        #
        # At the shipped `min_jd_chars: 0` this re-fetches the EMPTY subset only -- the
        # short-but-not-empty entries need a configured floor, which is the accepted cost
        # recorded in the spec's decision 3 and surfaced by `doctor` (Task 8).
        #
        # If the refetch also fails, nothing is written and this file lingers, inert,
        # re-read and re-rejected each run. That is the intended retry. There is
        # deliberately NO cleanup pass: deleting on a read would make a read a write,
        # which is the exact shape that disarmed the #81 relocation notice.
        return self.jd_arrived(cached)

    def get_or_build(self, lead: dict) -> dict:
        path = self._path(lead)
        if self._fresh(path):
            return json.loads(open(path, encoding="utf-8").read())
        enrich = self.fetcher(lead)
        jd = enrich.get("jd", {})
        dossier = {
            "schema_version": 2,
            "lead_id": self.cache_key(lead),
            "company": lead.get("company", ""),
            "position": lead.get("role", ""),
            "location": lead.get("location", ""),
            "role_type": lead.get("role_type", ""),
            # #223 §2.4: what the POSTING says about pay basis, as distinct from
            # `role_type` above, which is whatever the note already held -- in the
            # shape this issue is about, the label of the search that found the lead.
            # Read through `jd_text`, the same accessor the judge prompt and
            # `jd_arrived` use, so an observation cannot be derived from a different
            # slice of the payload than the one the pipeline calls the job description.
            #
            # No schema_version bump, matching `page_title`/`structured_data` two keys
            # below: every consumer reads it with `.get(...) or ""`, so a cache entry
            # written before this key existed stays valid rather than being thrown away
            # for a field that is optional by construction.
            "role_type_observed": observe_role_type(jd_text({"jd": jd})),
            "lead_snapshot": dict(lead),
            "jd": jd,
            "glassdoor": enrich.get("glassdoor", {}),
            # #109 tier-2 company resolution reads these two off a fresh dossier
            # directly; defaulting to "" here (not None) is what lets an OLD cached
            # dossier missing them entirely still parse via a plain .get(...) or "".
            "page_title": enrich.get("page_title", ""),
            "structured_data": enrich.get("structured_data", ""),
            "built_at": self.clock().isoformat(),
        }
        # Do NOT persist a fetch that produced no JD (#169). Caching one made every later
        # run serve the failure for the whole TTL: triage judged the lead on a document
        # nobody read, and because "unjudgeable" collapsed into `research`, the nightly
        # triage run over `_status.DEFAULT_TRIAGE_STATUSES` (triage/engine.py) re-selected
        # it and paid for the same non-answer until the entry expired. The judging half
        # of that loop is closed elsewhere on this branch (triage/engine.py
        # short-circuits before the judge call, and
        # `unjudgeable` is now its own status inside the default selection); what THIS
        # guard still buys is the REFETCH -- one per run, rather than a cached non-answer
        # no later run can get past. The FRESHLY FETCHED dossier is still returned, never
        # the rejected cached one, so the caller can answer `jd_arrived` on what it holds.
        if self.jd_arrived(dossier):
            os.makedirs(self.dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dossier, f, ensure_ascii=False)
        return dossier


# ── JSON-LD: the ONE walk and the ONE JobPosting predicate ───────────────────────────
# Shared because there were briefly TWO of each over the SAME `structured_data` field --
# `core/app.py`'s JD recovery (#228) and `triage/resolve.py`'s tier-2 company resolution
# (#109) -- and they measurably disagreed: for `{"@type": "https://schema.org/JobPosting"}`
# one returned the posting and the other abstained, on one page, from one blob. Two
# answers to one question is the drift this file exists to prevent, and the depth cap was
# a second copy of the same invariant with nothing asserting the copies agreed.
#
# Lives HERE rather than in app.py because it is dossier SEMANTICS -- what a page said
# about itself -- with no browser, no tab and no seam in sight. `_settle_body` stays in
# app.py for the mirror-image reason: it drives the Fetcher seam and is transport.
LD_MAX_DEPTH = 6


def iter_ld_nodes(data, depth: int = 0):
    """Every JSON object reachable in a JSON-LD payload, flattening arrays and `@graph`.

    Recursive rather than one-level because the capture side hands over an ARRAY of blocks
    (a real board emits several ld+json tags and the page's own JobPosting is often not the
    first), and any ONE block may itself be a bare object, an array, or a `@graph`
    container -- so a JobPosting can sit two levels down. A one-level walk reads a `@graph`
    wrapper's own (absent) `@type` and abstains, silently, on a page that did publish what
    was asked for.

    DEPTH-CAPPED because this is board-authored, untrusted input: `json.loads` survives
    nesting that a recursive walk does not, so without the cap a deeply nested payload
    raises RecursionError out of the `yield from` chain on a document the parser accepted.
    The cap is well past the deepest real shape (array -> block -> @graph -> node).
    """
    if depth > LD_MAX_DEPTH:
        return
    if isinstance(data, list):
        for item in data:
            yield from iter_ld_nodes(item, depth + 1)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if graph is not None:
            yield from iter_ld_nodes(graph, depth + 1)


def is_job_posting(kind) -> bool:
    """Is this `@type` value a schema.org JobPosting?

    Three spellings are legal and all three appear in the wild: the bare `JobPosting`, the
    fully-qualified `https://schema.org/JobPosting`, and the CURIE `schema:JobPosting`
    against a prefixed `@context`. An `endswith` test admitted all three and also admitted
    `NotAJobPosting`, letting an arbitrary node's content through; an exact `== "JobPosting"`
    rejects the two namespaced forms, which abstains on pages that did publish a posting.
    So the value is reduced to its last segment across all three separators and compared
    exactly -- the only form that accepts what schema.org allows and nothing that merely
    ends in the word.
    """
    if not isinstance(kind, str):
        return False
    last = kind.rsplit("/", 1)[-1].rsplit("#", 1)[-1].rsplit(":", 1)[-1]
    return last.strip() == "JobPosting"


def job_posting_types(node) -> bool:
    """`@type` is legally a LIST as well as a string, and a scalar check walks past
    `["JobPosting", "Thing"]`."""
    kind = node.get("@type") if isinstance(node, dict) else None
    kinds = kind if isinstance(kind, list) else [kind]
    return any(is_job_posting(k) for k in kinds)


def strip_html(raw: str) -> str:
    """HTML -> readable text. Block-level tags become newlines so lists stay legible.

    Stdlib only, and deliberately not a parser: this runs on a JSON-LD `description`, which
    is the page's own prose with light markup, not a document to be understood. The known
    limits of that choice are real and accepted -- an unclosed `<script>` leaks its source,
    an attribute containing `>` leaks its tail, CDATA leaks `]]>` -- and none is a
    regression, because this field was not text-extracted at all before.
    """
    # `</script >` and `</script\n>` are legal, and a pattern demanding the exact `</script>`
    # left the element's CODE in the text handed to the judge.
    text = re.sub(r"(?is)<(script|style)\b.*?</\s*\1\s*>", " ", raw)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", text)
    # TAG-SHAPED only: a name, a closing slash, a comment/declaration, or a processing
    # instruction. A bare `<[^>]*>` also eats ordinary prose -- a description reading
    # "teams of < 10 people" lost everything up to the next `>`, silently deleting real
    # content from a JD.
    text = re.sub(r"(?s)<(?:/\s*[A-Za-z][^>]*|[A-Za-z][^>]*|!--.*?--|![^>]*|\?[^>]*)>",
                  " ", text)
    # Unescape LAST so a tag written as entities (`&lt;script&gt;`) is not decoded into
    # markup ahead of the strip above and then matched by it. What it yields is TEXT: a `<`
    # that survives is a literal character in the posting, and nothing downstream parses
    # this string as HTML. Running it FIRST silently deletes real content -- measured, a
    # description mentioning `&amp;lt;canvas&amp;gt;` loses the word entirely.
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _same_posting(node_url, landed_url) -> bool:
    """Does a JSON-LD node's own url plausibly name the page we fetched?

    Compared on host + path only, case-folded, trailing slash ignored: a canonical url in
    metadata routinely differs from the landed one by query string, fragment, tracking
    parameters or scheme. A node with NO url is not judged here at all -- absence is not
    evidence, and most real postings omit it.
    """
    if not isinstance(node_url, str) or not node_url.strip() or not landed_url:
        return True
    def key(u):
        try:
            parts = urlsplit(u.strip())
        except ValueError:
            return None
        if not parts.netloc:
            return None
        return (parts.netloc.lower(), parts.path.rstrip("/").lower())
    a, b = key(node_url), key(landed_url)
    if a is None or b is None:
        return True
    return a == b


def jd_from_structured_data(raw, landed_url: str = "", log=None) -> str:
    """The JD carried by a page's own JSON-LD `JobPosting`, as text -- "" when there is none.

    WHY THIS EXISTS (#228). A client-rendered posting can settle into NAVIGATION CHROME
    rather than the job: measured on a live posting from one of the two ATS vendors in the
    issue, the settled body was a short run of navigation text while the same page's JSON-LD
    carried the real description, an order of magnitude longer. Chrome is non-empty, so
    `jd_arrived` accepts it at the shipped `min_jd_chars` of 0 -- the settle turned an honest
    empty-and-flagged failure into a silent one, and this takes it back. The blob is already
    fetched for #109, so recovery costs no extra probe, no HTTP client and no per-host list.

    TIED TO THE LEAD BY URL. `_LD_JSON_JS` collects EVERY ld+json block, and a posting page
    routinely carries a neighbour's JobPosting -- a related-roles rail, a board widget. A
    node whose OWN `url`/`@id` names a different posting is dropped: it carries the evidence
    that disqualifies it, and without this a single such node became the lead's JD and
    silently tailored a CV to another job. Url-less nodes stay eligible, since most real
    postings omit the field and absence is not evidence.

    ABSTAINS ON AMBIGUITY. With two distinct descriptions still standing there is no way to
    tell the lead's from a neighbour's, so it returns "" rather than guessing. One candidate
    is an answer; two is a question.

    BEST-EFFORT, like the probe that captured it: every malformed shape degrades to "". This
    runs inside the dossier fetch, and raising would discard a JD already read from the page.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        doc = json.loads(raw)
    except Exception as exc:
        # `except Exception`, not a two-type tuple: this promises to degrade rather than
        # raise, and it is called outside the caller's `finally`, so an escaping exception
        # discards a JD ALREADY read. Which end raises is interpreter-specific -- measured,
        # `json.loads` parses 20000 levels on CPython 3.14's iterative C scanner and raises
        # on 3.12/3.13 -- so this defends the contract rather than one build's behaviour.
        if log:
            log.warning("dossier JSON-LD did not parse (%s), no JD recovered from it",
                        type(exc).__name__)
        return ""

    found = []
    for node in iter_ld_nodes(doc):
        if not job_posting_types(node):
            continue
        if not _same_posting(node.get("url") or node.get("@id"), landed_url):
            continue
        desc = node.get("description")
        if not isinstance(desc, str):
            continue
        text = strip_html(desc)
        if text and text not in found:
            found.append(text)

    if len(found) == 1:
        return found[0]
    if len(found) > 1 and log:
        # Named rather than silent: the page DID publish postings and the code declined to
        # choose, which is a state a human could resolve and would otherwise never hear
        # about -- the same "degrading, but not silently" posture the probes take.
        log.warning("dossier JSON-LD carried %d distinct JobPosting descriptions and none "
                    "could be tied to this lead; abstaining rather than guessing", len(found))
    return ""
