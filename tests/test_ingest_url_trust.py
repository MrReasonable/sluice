"""Both halves of the "an extractor trusted a URL it never checked" class (#160, #153).

Two DIFFERENT failure modes that a single audit surfaced, kept in one file because the
sweep that finds them is shared:

  #160 CONTENT.   A field built from a URL segment reaches the vault without being
                  percent-decoded. cord derived `company` from the path slug and decoded
                  only `title`, so a company named `Example?` -- served as the slug
                  `example%3F` -- was stored under its encoded spelling. Not cosmetic:
                  `track` matches mail against the stored company name, so already-resolved
                  threads re-surfaced as unmatched on every pass.

  #153 ADMISSION. A row is taken without checking where its link points. reed interleaves
                  sponsored COURSE cards into the jobsearch results page and the
                  extractor's link cascade ends in "any anchor in the card", so courses
                  became leads that park in `needs_review` forever.

The asymmetry between them is why the fixes differ. #160 corrupts a field and can lose
nothing; #153's fix is a FILTER, and a filter is what silently bins real leads -- so its
default abstains and its rejections are counted rather than dropped.
"""
import dataclasses
import re

import pytest

from sluice.ingest import sources as S
from sluice.ingest.base import BrowserListSource, Search, admits_path

_SEARCH = Search(label="s", url="https://example.invalid/s", params=None)


# ── #153: the path guard ──────────────────────────────────────────────────────

def test_an_undeclared_source_admits_everything():
    """The abstain default, and the single most important row in this file.

    Every source but `reed` declares no `posting_paths` today, so the guard must be
    invisible to them -- a shipped default, or "reject what we do not recognise", is the
    `672ad2a` shape: a gate that rejects when unconfigured bins a real job hunt and the
    person it happens to cannot see why their board went quiet.
    """
    for url in ("https://example.invalid/courses/x/1",
                "https://example.invalid/anything",
                "https://example.invalid/",
                ""):
        assert admits_path((), url) is True, f"an undeclared source must admit {url!r}"


def test_a_declared_source_admits_its_own_paths_and_rejects_others():
    admits = ("/jobs/",)
    assert admits_path(admits, "https://example.invalid/jobs/rust-developer/123")
    assert not admits_path(admits, "https://example.invalid/courses/rust/1")
    # A query string is not part of the path, so the `itm_source=js_search_results` marker
    # the observed course URLs carried must not rescue them.
    assert not admits_path(
        admits, "https://example.invalid/courses/php-developer-training/2"
                "?itm_source=js_search_results&jobs=1")


def test_a_blank_url_abstains_rather_than_being_rejected():
    """A missing link is a DIFFERENT defect, already measured by the engine's `link_rate`.

    Rejecting it here would hide a link-extraction failure behind a path verdict it never
    earned -- the guard would report "wrong path" for a row that has no path at all.
    """
    for url in ("", "   ", None):
        assert admits_path(("/jobs/",), url or "") is True


def _parse_path_classes():
    """Every source CLASS in the LIVE REGISTRY that parses rows and reports health.

    Derived from the registry, NOT from `vars(sluice.ingest.base)`. That first version
    reproduced the very failure this file is about: four registered sources subclass
    `BrowserListSource` from their own modules, and three of them override `parse` or
    `health_hint`. A module-scoped sweep cannot see any of them, so it certified the guard
    over 2 of 6 classes while reading exactly as if it covered them all -- the same "a
    sweep that finds nothing for something it never looked at" shape that made the first
    decode sweep skip `wttj`.

    Those three overrides all delegate to `super()` today, so the guard does reach them --
    but by good manners, not by enforcement. Sweeping the real classes is what turns that
    into something a future non-delegating override cannot quietly undo.
    """
    return sorted({type(src) for src in S.all_sources()}, key=lambda c: c.__name__)


_PARSE_PATH_CLASSES = _parse_path_classes()


def _minimal(cls, **kw):
    """`cls` with every REQUIRED field filled by a placeholder, so these tests carry no
    per-class constructor recipe to go stale as fields move."""
    args = {f.name: ([] if f.name.endswith("_spec") else "x")
            for f in dataclasses.fields(cls)
            if f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING}
    return cls(**{**args, **kw})


def _payload_key(cls):
    """Which raw key this class's `health_hint` counts rows under -- PROBED, not assumed.

    The first version of the report sweep fed BOTH keys at once (`{"result": rows, "jobs":
    rows}`) so it would not need to know. That hid the bug it existed to catch: a class
    counting rejections off the WRONG key passed every assertion, because both keys held
    the same rows -- the copy-paste failure that put this sweep here in the first place.
    The candidate list is small and the assertion pins that exactly one matches, so a class
    with a third key fails loudly here instead of being silently swept as if it had none.
    """
    src = _minimal(cls, id="s")
    keys = [k for k in ("result", "jobs")
            if src.health_hint({k: [{"title": "a"}]}).get("count") == 1]
    assert len(keys) == 1, (
        f"{cls.__name__}.health_hint counts {keys or 'NO'} known payload key(s); this sweep "
        "would test it against a payload it never reads")
    return keys[0]


def test_the_class_sweep_is_not_looking_at_an_empty_set():
    """SCOPE, asserted before any verdict derived from it. Every sweep below is
    parameterised over `_PARSE_PATH_CLASSES`, and a discovery filter that matches nothing
    makes all of them pass vacuously -- `all([])` is `True`. Pinning the names is a RATCHET,
    not a roster: a new parse path fails this row and forces someone to read it, which is
    the opposite of a list that silently stays short.
    """
    names = {c.__name__ for c in _PARSE_PATH_CLASSES}
    # One base class since 2026-08-28, when `CarouselSource` was retired with its last
    # producer (wttj moved to WTTJ's list view). This asserted BOTH were present, which was
    # the anti-vacuity guard for a two-implementation seam; with one implementation the
    # equivalent claim is that the base class is reached at all, plus at least one subclass
    # that OVERRIDES `parse` -- otherwise the sweeps below only ever see inherited behaviour
    # and would certify nothing about the overrides they exist for.
    assert "BrowserListSource" in names, (
        f"the registry's parse-path classes are now {sorted(names)} and no longer include the "
        "base class -- the sweeps below would silently stop covering it")
    assert len(names) > 1, (
        f"only {sorted(names)} reached the sweep -- no source overrides `parse`, so every "
        "row below is testing inherited behaviour only")
    # `_ReedSource` joined 2026-08-27: reed's company recovery moved out of the extractor JS
    # and into a `parse` override, so it became a parse path. Read against the sweeps below
    # before being added here, per this docstring's instruction: it subclasses
    # `BrowserListSource` without re-declaring any field and delegates to `super().parse`, so
    # it inherits `posting_paths` (default `()`), the `health_hint` rejection count and the
    # validator -- the same shape as `_NaukrigulfSource` and `WellfoundSource`, both of which
    # already pass every row below on exactly that basis.
    assert names == {"BrowserListSource", "WellfoundSource",
                     "_LinkedInSource", "_NaukrigulfSource", "_ReedSource",
                     "_WorkInStartupsSource"}, (
        f"the registry's parse-path classes are now {sorted(names)}; every sweep "
        "parameterised over them covers a different set than when it was written, so "
        "re-read the new one before updating this row")


@pytest.mark.parametrize("cls", _PARSE_PATH_CLASSES, ids=lambda c: c.__name__)
def test_every_parse_path_declares_the_guard_field(cls):
    """Enumerated over the source CLASSES, not written once for the one that had the bug.

    `parse` reads `self.posting_paths`, so a class carrying the filter without the field
    raises `AttributeError` on every row -- measured, not assumed. That is precisely the
    state the code was in between adding the guard to `parse` and adding the field to the
    second class, which is why this asserts over both rather than naming one.
    """
    names = {f.name for f in dataclasses.fields(cls)}
    assert "posting_paths" in names, (
        f"{cls.__name__}.parse filters on self.posting_paths but the dataclass does not "
        "declare it -- every row will raise AttributeError")
    assert cls.__dataclass_fields__["posting_paths"].default == (), (
        f"{cls.__name__}.posting_paths must default to the ABSTAINING empty tuple")


def test_reed_rejects_a_course_card_and_keeps_the_job():
    """The #153 reproduction, through the REAL registered source rather than a stand-in.

    Both course URLs are the shapes named in the issue, `itm_source=js_search_results` and
    all -- they are served by the jobsearch results page, which is why the extractor saw
    them in the first place.
    """
    reed = S.get("reed")
    # The board HOST is kept -- it names whose path grammar `reed.posting_paths` declares,
    # and `tests/test_health.py` already uses registered board domains the same way. The
    # slugs and ids are SYNTHETIC: they came from an observed scrape and carry nothing the
    # assertion needs, since `admits_path` reads `urlsplit().path` and never the slug.
    raw = {"result": [
        {"title": "Example course A",
         "link": "https://www.reed.co.uk/courses/example-course-a/1"
                 "?itm_source=js_search_results"},
        {"title": "Example course B",
         "link": "https://www.reed.co.uk/courses/example-course-b/2"
                 "?itm_source=js_search_results"},
        {"title": "Example Role", "company": "Example Ltd",
         "link": "https://www.reed.co.uk/jobs/example-role/3"},
    ]}
    titles = [lead.title for lead in reed.parse(raw, _SEARCH)]
    assert titles == ["Example Role"], f"courses should not become leads: {titles}"


def test_a_rejection_is_reported_rather_than_silent():
    """The half that keeps the filter from being invisible.

    Producing the key is only half the guard; `test_a_rejection_reaches_a_reader` below is
    the other half. A board renaming `/jobs/` to `/job/` must read as "reed rejected 20 of
    20 on their path", not as "reed has gone quiet".
    """
    reed = S.get("reed")
    raw = {"result": [
        {"title": "A course", "link": "https://www.reed.co.uk/courses/x/1"},
        {"title": "A job", "link": "https://www.reed.co.uk/jobs/y/2"},
    ]}
    hint = reed.health_hint(raw)
    assert hint["count"] == 2, "count stays RAW rows -- it is the denominator"
    assert hint["rejected_paths"] == 1


@pytest.mark.parametrize("cls", _PARSE_PATH_CLASSES, ids=lambda c: c.__name__)
def test_every_parse_path_reports_what_it_rejected(cls):
    """The REPORT half, enumerated over the classes exactly as the filter half is.

    This is the gap that actually shipped on this branch: `parse` filtered on both classes
    and `health_hint` reported on one, so a mis-declared prefix on a carousel source would
    have dropped rows with nothing anywhere saying so -- the silent filter `rejected_paths`
    exists to prevent, reproduced inside the fix for it.
    """
    rows = [{"title": "A course", "link": "https://example.invalid/courses/x/1"},
            {"title": "A job", "link": "https://example.invalid/jobs/y/2"}]
    # Under this class's OWN key only. Populating both (the first version) meant a class
    # counting rejections off the other class's key still passed -- see `_payload_key`.
    raw = {_payload_key(cls): rows}

    hint = _minimal(cls, id="s", posting_paths=("/jobs/",)).health_hint(raw)
    assert hint["count"] == 2, "count stays RAW rows -- it is the denominator"
    assert hint.get("rejected_paths") == 1, (
        f"{cls.__name__}.parse filters on posting_paths but health_hint does not report it "
        "-- the filter is SILENT on this class")

    # And the abstaining default stays byte-identical here too: ABSENT, not zero, because
    # `detect_drift` classifies on the keys a hint carries.
    assert "rejected_paths" not in _minimal(cls, id="s").health_hint(raw), (
        f"{cls.__name__} emits the key without declaring posting_paths -- every source that "
        "never opted in would report a new key to `detect_drift`")


@pytest.mark.parametrize("cls", _PARSE_PATH_CLASSES, ids=lambda c: c.__name__)
@pytest.mark.parametrize("bad,expected", [
    # The message is asserted, not just the TYPE. Both arms of the validator raise
    # `ValueError` mentioning `posting_paths`, so a type-only assertion cannot tell which
    # one fired -- witnessed: deleting the string arm left this test GREEN, because
    # `"/jobs/"` then falls through and trips the prefix loop on the character `'j'`.
    ("/jobs/", "not a string"),
    # And this row is why the string arm is load-bearing rather than merely a nicer
    # message: `"/"` is a bare string whose CHARACTERS are all valid prefixes, so the
    # prefix loop accepts it outright and the guard becomes inert -- `urlsplit().path`
    # always starts with `/`, so every url is admitted and #153 is fully back.
    ("/", "not a string"),
    (("jobs/",), "starting with '/'"),
    (("/jobs/", 7), "starting with '/'"),
], ids=["bare-string", "bare-string-all-valid-chars", "no-leading-slash",
        "non-string-prefix"])
def test_a_misdeclared_posting_paths_raises_at_construction(cls, bad, expected):
    """FAIL LOUDLY AT CONSTRUCTION -- and note the two directions fail OPPOSITE ways, which
    is why neither announces itself. Measured on the real predicate before this guard:

      `posting_paths=("/jobs/")`  admits every url, so the guard is INERT and #153's course
                                  cards come straight back -- nothing red anywhere.
      `posting_paths=("jobs/",)`  matches nothing, so 100% of that board's postings are
                                  binned -- the `672ad2a` harm, delivered by one typo.

    Swept over the source CLASSES rather than written for one, for the same reason as its
    siblings: the field lives on both base classes and is inherited by four more.
    """
    with pytest.raises(ValueError) as exc:
        _minimal(cls, id="s", posting_paths=bad)
    msg = str(exc.value)
    assert expected in msg, (
        f"raised, but not from the arm this row is about -- got {msg!r}. Asserting only "
        "the exception TYPE would pass here while the arm under test was gone.")
    # The message must NAME the offending source, or an operator with 22 registered sources
    # is told only that one of them is wrong.
    assert "source s" in msg


@pytest.mark.parametrize("cls", _PARSE_PATH_CLASSES, ids=lambda c: c.__name__)
def test_a_one_shot_iterable_is_normalised_rather_than_stored_and_exhausted(cls):
    """Validating a COPY while the field keeps the original is its own bug.

    `validate_posting_paths` materialises `tuple(posting_paths)` to inspect it. When that
    copy was discarded, a generator passed validation and was then EXHAUSTED on the field:
    `admits_path` reads a spent generator as TRUTHY, so the abstain arm never fires and
    `any(...)` over nothing rejects every row. Measured before the fix -- a genuine posting
    dropped, zero leads -- which is the `672ad2a` harm arriving through the validator
    written to prevent it.

    A list is included because normalising is what makes it safe to accept one at all.
    """
    for declared in ((p for p in ("/jobs/",)), ["/jobs/"], ("/jobs/",)):
        src = _minimal(cls, id="s", posting_paths=declared)
        assert isinstance(src.posting_paths, tuple), (
            f"{cls.__name__} stored {type(src.posting_paths).__name__}, which may be "
            "single-use; a second read would reject every posting")
        # Re-read it TWICE: the whole failure mode is that the first read consumes it.
        url = "https://example.invalid/jobs/y/2"
        assert admits_path(src.posting_paths, url)
        assert admits_path(src.posting_paths, url), "second read saw an exhausted iterable"


def test_the_registry_itself_declares_only_usable_posting_paths():
    """The sweep over what actually SHIPS, not over what a test constructs.

    `__post_init__` already raises at import time, so a bad declaration cannot reach here --
    which is exactly why this asserts its own SCOPE first. A registry that failed to import
    would otherwise leave an empty loop that passes, reporting "every declaration is fine"
    about a set it never examined.
    """
    srcs = S.all_sources()
    assert srcs, "registry empty -- this sweep would certify nothing"
    declaring = {s.id: s.posting_paths for s in srcs if s.posting_paths}
    assert declaring, (
        "no registered source declares posting_paths, so this sweep is vacuous -- if the "
        "last declaration was removed deliberately, remove this test with it")
    for sid, paths in declaring.items():
        assert not isinstance(paths, str), f"{sid}: posting_paths is a bare string"
        for prefix in paths:
            assert prefix.startswith("/"), f"{sid}: {prefix!r} does not start with '/'"


def test_a_rejection_reaches_a_reader():
    """The half that was MISSING, and without which everything above is decoration.

    Producing `rejected_paths` is not the same as reporting it. Shipped in review, the key
    was written into the health hint and read by NOTHING -- not `detect_drift`, not
    `_print_report`, not `cmd_health` -- so a board renaming `/jobs/` gave zero leads, a
    healthy-looking `count`, `drift=None`, and no way at all to find out why. That is the
    silent filter the whole design claims to avoid, so the claim needs this row.
    """
    from sluice.core.health import detect_drift
    # Every row rejected: an unambiguous grammar change, and the one case the gate fires on.
    assert detect_drift("s", 20, {"rejected_paths": 20}, 20.0) == "paths"
    # reed's STEADY STATE -- a few sponsored course cards on an otherwise healthy page.
    # This must not fire, or the guard cries wolf on every single run and gets turned off.
    assert detect_drift("s", 20, {"rejected_paths": 2}, 20.0) is None
    # And a source that never opted in is classified exactly as it was before this existed.
    assert detect_drift("s", 20, {}, 20.0) is None


def test_a_rejection_is_summed_across_searches_rather_than_last_search_wins():
    """`signals` is REASSIGNED per search, so a per-search key is silently overwritten.

    Measured before the fix: a two-search source whose FIRST search had every row rejected
    and whose SECOND came back clean reported no rejection at all. `degraded` and
    `login_paths` are sticky for this exact reason; a COUNT has to be summed rather than
    frozen first-found, because it is the numerator whose denominator is `count` -- and both
    are accumulated off the same hint on the same line so the ratio `detect_drift` takes
    cannot straddle two populations.
    """
    from sluice.ingest.engine import _run_source

    class _TwoSearch(BrowserListSource):
        def fetch(self, ctx, search):
            rows = ([{"title": "A course", "link": "https://example.invalid/courses/x/1"}]
                    if search.label == "first"
                    else [{"title": "A job", "link": "https://example.invalid/jobs/y/2"}])
            return {"result": rows, "landed": search.url, "requested": search.url}

    src = _TwoSearch(
        id="two", extractor_js="x", posting_paths=("/jobs/",),
        searches_spec=[("first", "https://example.invalid/a"),
                       ("second", "https://example.invalid/b")])
    result = type("R", (), {"fetched": 0, "status": "ok", "error": None,
                            "rejected_paths": 0})()
    from sluice.ingest.base import Ctx
    ctx = Ctx(camofox=None, config=None, sleep=lambda *_: None)
    count, signals = _run_source(src, ctx, set(), [], result,
                                 fetch_timeout=5, retries=1)
    assert count == 2, "count sums RAW rows across searches -- it is the denominator"
    assert signals.get("rejected_paths") == 1, (
        "the first search's rejection was overwritten by the second search's clean signals")


def test_an_undeclared_source_emits_no_rejection_key_at_all():
    """Absent, not zero. `detect_drift` classifies on the keys a hint carries, so a new
    key present on every source would change what every source reports -- the guard must
    be byte-identical for the 21 sources that never opted in."""
    cord = S.get("cord")
    raw = {"result": [{"title": "x", "link": "https://cord.com/anything/at/all"}]}
    assert "rejected_paths" not in cord.health_hint(raw)


# ── #160: the decode-symmetry sweep ───────────────────────────────────────────

# A capture group that cannot carry a percent-encoding needs no decode. Digits are the
# only such shape in the registry today (`eighty_k` captures `jobId=(\d+)`), and it is
# listed as a PATTERN rather than a source name so a second numeric-capture source is
# exempt automatically instead of needing a roster entry.
_UNENCODABLE_CAPTURE = re.compile(r"^\(\\d\+\)$|^\(\[0-9\]\+\)$")
# The match BINDING, so the variable name is derived rather than assumed. Keying on `m`
# was measured blind to `wttj` (binds `sm`) and `workinstartups` (binds `salMatch`).
_BINDING_RE = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*[^;]*?\.match\(/(.+?)/[gimsuy]*\)")
_CAPTURES = re.compile(r"\((?!\?:)[^)]*\)")


def _js_blobs(src):
    """Every JS attribute a source carries, found by SUFFIX rather than by name.

    `CarouselSource` calls its blob `read_js`, not `extractor_js` -- keying on the latter
    made the first version of this sweep skip wttj entirely and report it clean, which is
    the "a search that finds nothing proves nothing" failure this file exists to prevent.
    """
    return {n: getattr(src, n) for n in dir(src)
            if n.endswith("_js") and isinstance(getattr(src, n, None), str)
            and getattr(src, n).strip()}


def _url_derived_fields(js):
    r"""`[(capture_pattern, statement, is_decoded)]` for each `<var>[N]` read out of a match.

    Three things here were wrong in the first version, each MEASURED against a real
    offender rather than reasoned about, and each failing in the silent direction:

    STATEMENTS, not lines. Splitting on newlines meant the identical #160 bug joined onto
    one line reported `offenders == []`. That is not hypothetical minification: `eighty_k`
    already packs three statements per line today.

    The match VARIABLE derived from its own binding, not assumed to be `m`. `wttj` binds
    `sm` and `workinstartups` binds `salMatch`, so both were invisible to the sweep that
    claimed to cover every source.

    Groups tracked PER BINDING, not flattened across the blob. Two `.match()` calls in one
    source desynchronised the flat list, so an undecoded company inherited the numeric
    capture `(\d+)` from an unrelated match and was FALSELY EXEMPTED -- a guard actively
    certifying the bug it exists to find.

    `is_decoded` is decided at the USE SITE (`decodeURIComponent(<var>[N]`), not by asking
    whether the statement mentions a decode ANYWHERE: `const a=decodeURIComponent(m[2])+m[1]`
    decodes one capture and not the other. Requiring the call to wrap this specific use is
    strict, and deliberately so -- an over-strict check reports a clean field as an offender,
    which is LOUD and gets fixed, while a lax one certifies a real one, which is how #160
    shipped.
    """
    groups_by_var = {var: _CAPTURES.findall(pattern)
                     for var, pattern in _BINDING_RE.findall(js)}
    out = []
    for stmt in js.split(";"):
        for var, idx in re.findall(r"\b(\w+)\[(\d+)\]", stmt):
            if var not in groups_by_var:
                continue          # an array index into something that is not a match result
            n = int(idx)
            if n == 0:
                continue          # [0] is the whole match, a dedup key here
            groups = groups_by_var[var]
            pattern = groups[n - 1] if n - 1 < len(groups) else "<unknown>"
            decoded = re.search(
                r"decodeURIComponent\(\s*" + re.escape(var) + r"\[" + str(n) + r"\]", stmt)
            out.append((pattern, stmt.strip(), bool(decoded)))
    return out


def test_every_url_derived_field_is_percent_decoded():
    """The #160 class, swept across every registered source.

    This is a SOURCE-TEXT check, and that is not a shortcut -- it is the only executable
    option. The golden fixtures under `tests/fixtures/` are the extractor's OUTPUT, captured
    after the JS ran, so no offline test can exercise the JS itself; running it would need a
    browser, which the suite deliberately never touches. Asserting the symmetry in the source
    catches exactly the defect that shipped: cord decoded `title` and not `company`, and the
    two sat on adjacent lines for months.
    """
    srcs = S.all_sources()
    assert srcs, "registry empty -- this sweep would certify nothing"

    examined, with_captures, offenders = 0, 0, []
    for src in sorted(srcs, key=lambda s: s.id):
        blobs = _js_blobs(src)
        assert blobs, f"{src.id} carries no JS at all -- inspect it by hand rather than skipping"
        examined += 1
        for name, js in blobs.items():
            uses = _url_derived_fields(js)
            if uses:
                with_captures += 1
            for pattern, stmt, decoded in uses:
                if _UNENCODABLE_CAPTURE.match(pattern):
                    continue
                if not decoded:
                    offenders.append(f"{src.id}.{name}: capture {pattern} used undecoded -> {stmt}")

    # SCOPE, asserted before the verdict: `all([])` is `True`, so an offender list that is
    # empty because nothing was examined reads identically to one that is empty because
    # everything is clean.
    assert examined == len(srcs), f"examined {examined} of {len(srcs)} sources"
    assert with_captures >= 2, (
        f"only {with_captures} JS blob(s) derive a field from a URL capture -- cord and "
        "eighty_k both do, so the extraction has drifted and this sweep is now vacuous")
    assert not offenders, (
        "a field built from a URL segment reaches the vault without percent-decoding. "
        "cord shipped this for months -- `company` undecoded beside a decoded `title` on "
        "adjacent lines -- and stored company names in their encoded spelling:\n  "
        + "\n  ".join(offenders))


def test_the_decode_sweep_catches_the_bug_it_was_written_for():
    """POSITIVE CONTROL. Without it the sweep above passes just as happily on a registry
    whose JS it can no longer parse -- and this specific sweep has already been wrong once,
    silently skipping the one source whose blob is named `read_js`."""
    broken = "const m=h.match(/\\/u\\/([^\\/]+)\\/jobs\\/(\\d+)/);const company=m[1].replace(/-/g,' ');"
    uses = _url_derived_fields(broken)
    assert uses, "the parser found no URL-derived field in text that plainly has one"
    pattern, _stmt, decoded = uses[0]
    assert not _UNENCODABLE_CAPTURE.match(pattern), f"{pattern} wrongly treated as unencodable"
    assert not decoded, "the control sample must be an OFFENDER"


_UNDECODED = "const company=%s[1].replace(/-/g,' ');"
_MATCH = "const %s=h.match(/\\/u\\/([^\\/]+)\\/jobs\\/([^?]+)/)"


@pytest.mark.parametrize("label,js", [
    # The shape the sweep was originally written against -- the control for the rest.
    ("multi-line", f"{_MATCH % 'm'};\n{_UNDECODED % 'm'}"),
    # MINIFIED. The first sweep split on newlines, so joining the identical bug onto one
    # line reported zero offenders. `eighty_k` already packs three statements per line, so
    # this style is present in the registry today rather than hypothetical.
    ("minified onto one line", f"{_MATCH % 'm'};{_UNDECODED % 'm'}"),
    # A match bound to something other than `m`. `wttj` binds `sm`, `workinstartups` binds
    # `salMatch`; a sweep keyed on the name `m` is blind to both while reporting them clean.
    ("bound to a variable that is not `m`", f"{_MATCH % 'sm'};{_UNDECODED % 'sm'}"),
    # TWO matches in one blob. The first sweep flattened every capture into one list, so
    # this undecoded company inherited the numeric `(\d+)` from the unrelated match above
    # it and was FALSELY EXEMPTED -- the sweep certifying the exact bug it exists to catch.
    ("two .match() calls desyncing the groups",
     f"const idm=h.match(/jobId=(\\d+)/);const id=idm[1];{_MATCH % 'm'};{_UNDECODED % 'm'}"),
    # A decode present in the statement but wrapping the OTHER capture. Asking whether the
    # statement mentions `decodeURIComponent` anywhere passes this; asking whether it wraps
    # THIS use does not.
    ("decode on one capture but not the other",
     f"{_MATCH % 'm'};const a=decodeURIComponent(m[2])+m[1];"),
], ids=lambda v: v if isinstance(v, str) and " " in v else "")
def test_the_decode_sweep_catches_every_offender_shape_in_this_registry(label, js):
    """REGRESSION ROWS for a guard that failed OPEN on all four of the last shapes.

    Every one of these carries the same defect -- a capture used to build a field with no
    decode -- and every one was measured returning `offenders == []` before this rewrite.
    A guard that finds nothing reads exactly like a codebase that is clean, which is why
    these are pinned individually rather than covered by one happy-path row.
    """
    offenders = [(pattern, stmt) for pattern, stmt, decoded in _url_derived_fields(js)
                 if not _UNENCODABLE_CAPTURE.match(pattern) and not decoded]
    assert offenders, f"{label}: an undecoded URL-derived field was reported as clean"


def test_a_numeric_capture_is_exempt_without_needing_a_roster_entry():
    """`eighty_k` captures `jobId=(\\d+)`. Digits cannot carry a percent-encoding, so
    requiring a decode there would be noise -- and the exemption is keyed on the capture
    PATTERN, so a second numeric-capture source is covered without anyone adding it to a
    list that would then need maintaining."""
    numeric = "const m=h.match(/jobId=(\\d+)/);const id=m[1];"
    (pattern, _stmt, _decoded), = _url_derived_fields(numeric)
    assert _UNENCODABLE_CAPTURE.match(pattern), f"{pattern} should be exempt"
    assert not _decoded, "premise: the exempt use is genuinely undecoded"
