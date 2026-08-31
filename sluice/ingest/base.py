"""The Source contract and the base class that covers almost every board.

A Source splits impure I/O (`fetch`, which drives the browser) from a pure
transform (`parse`, raw dict -> list[Lead]) so parsers are tested offline against
golden fixtures with no Camofox. `BrowserListSource` covers scroll-a-list boards.
Anything weirder subclasses / duck-types `Source` directly -- `wellfound`,
`naukrigulf`, `reed`, `linkedin` and `workinstartups` all do, by overriding one of
the two sanctioned hooks rather than by needing a second base class.

There WAS a second one. `CarouselSource` read a one-job-at-a-time carousel by
clicking an advance control, and was retired on 2026-08-28 when its only producer
(`wttj`) moved to WTTJ's list view and left it with none. It is recorded here
rather than silently dropped because the shape is real and a future board may
want it back: read the visible job, advance, stop when a read repeats or the
control disappears. `git log -- sluice/ingest/base.py` has the implementation.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from urllib.parse import urlparse

from sluice.core.config import validate_search_entry
from sluice.core.leads import Lead

HealthSignals = dict


@dataclass
class Search:
    label: str
    url: str | None = None
    params: dict | None = None
    # Did this search come from the user's `sources.<id>.searches`, or is it the
    # source's shipped example? (#212) The default is False because that is what a
    # shipped example IS -- so every existing construction, including
    # `BrowserListSource.searches()`, stays correct without being touched, and a
    # future search-producing path that forgets to think about provenance is
    # treated as the tool's guess rather than the user's assertion. The direction
    # matters: #223 lets a `configured` search's `params` drive a pay floor, so a
    # wrong True is a shipped preference wearing the user's authority.
    configured: bool = False


@dataclass
class Ctx:
    """What a source needs to run: the browser client, the loaded config, and an
    injectable sleep so tests don't actually wait for page settle."""
    camofox: object
    config: object = None
    # None means "nothing to inject, give me the real one" -- the same tolerance
    # VaultSink(today=None) already has. Without it a caller holding an optional
    # sleep must build a conditional kwargs dict, and the obvious tidy-up of that
    # (`sleep=self._sleep`) passes None straight through: the suite stays green
    # while every real ingest run dies on the first `ctx.sleep(wait)`. Cheaper to
    # make the value safe here than to guard every construction site.
    sleep: Callable | None = None

    def __post_init__(self):
        if self.sleep is None:
            self.sleep = time.sleep


class Source(Protocol):
    """A job board plugin: what to search, how to fetch results, and how to parse
    them into Leads. `fetch` is the only impure member -- it drives a `Ctx`'s
    browser client; `parse` is pure, tested offline against golden fixtures under
    tests/fixtures/<id>/raw.json.

    OPTIONAL MEMBER -- `company_from_url(self, url: str) -> str | None`. Not
    declared as a required member below, for the identical reason `Store.preflight`
    and `Renderer.precheck` are not: a Protocol member is a REQUIRED member, and the
    whole point of this hook is that a source may omit it.
    `sluice.triage.resolve.resolve_company` (#109) reaches it via
    `getattr(source, "company_from_url", None)` and treats its absence as tier-1
    abstaining for that source -- the same shape those two other optional seam
    members already use.

    Implement it only where the board's real URL shape unambiguously encodes the
    hiring company with a clear delimiter on both ends of the captured slug --
    never a guessed split point. Must never raise: it runs against live,
    hand-maintained scraped URLs on every triage run, so `resolve_company` isolates
    any exception from it and treats that as an abstain rather than letting one
    source's bug on one unanticipated URL shape crash the whole batch.
    """

    id: str
    enabled: bool
    kind: str

    def searches(self) -> list: ...
    def fetch(self, ctx: Ctx, search: Search) -> dict: ...
    def parse(self, raw: dict, search: Search) -> list: ...
    def health_hint(self, raw: dict) -> dict: ...


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _path(url: str) -> str:
    # Mirrors `_host`: unconditional `""` on failure, never omitted. A path is a
    # measurement that always exists (possibly empty), not an event that "fired" -- the
    # same reasoning `landed_host`/`requested_host` already follow. #156's `login` drift
    # reason (`core/health.py`) needs this pair: a redirect to a login wall on the SAME
    # host is invisible to a host-only comparison, and that is exactly the incident that
    # motivated it -- `/jobs?query=...` -> `/login?redirect=%2F`, host unchanged.
    try:
        return urlparse(url).path or ""
    except Exception:
        return ""


def admits_path(posting_paths, url: str) -> bool:
    """Does `url` point at a POSTING, per the source's own declared path prefixes?

    ABSTAINS when `posting_paths` is empty, and that default is the whole safety story
    (#153). A board that declares nothing admits every row exactly as it did before this
    existed, so 21 of the 22 sources are byte-identical and only a source that opts in
    filters at all. The inverse -- a shipped default list, or "reject what we do not
    recognise" -- is the `672ad2a` shape this codebase most consistently engineers out: a
    gate that rejects when unconfigured silently bins a real job hunt, and the person it
    happens to cannot see why their board went quiet.

    An ALLOWLIST of prefixes rather than a `/courses/` denylist, per #153's own reasoning:
    a denylist closes only the case already observed, and reed interleaves whatever it
    likes into a results page. The cost is that a board changing its posting path breaks
    ingestion for that source -- which is why rejections are COUNTED and reported (see
    `rejected_paths` in `health_hint`) rather than dropped in silence. A source returning
    20 rows and 0 leads must say why.

    A row whose url is blank abstains rather than being rejected: a missing link is a
    DIFFERENT defect, already measured by the engine's own `link_rate`, and swallowing it
    here would hide it behind a path verdict it never earned.
    """
    if not posting_paths:
        return True
    if not url or not str(url).strip():
        return True
    path = _path(str(url))
    return any(path.startswith(prefix) for prefix in posting_paths)


def _rejected_path_count(posting_paths, rows) -> int:
    """How many of `rows` `admits_path` would reject. Computed on the RAW rows and from
    the same predicate `parse` filters with, so the report cannot disagree with the
    behaviour -- the identical reasoning `_first_degraded` states for reading row markers
    pre-parse rather than post."""
    if not posting_paths:
        return 0
    return sum(
        1 for row in _sized(rows)
        if isinstance(row, dict)
        and not admits_path(posting_paths, row.get("link") or row.get("url") or "")
    )


# `searches_spec`'s own contract grammar -- the third field `BrowserListSource` validates
# at construction, beside `posting_paths` and `reprobed` below -- is NOT here: it is
# `core/config.py`'s `validate_search_entry`, called from `BrowserListSource.__post_init__`
# further down. No `core/` module imports a sub-app at MODULE SCOPE (measured: 22 core
# modules, 0 such imports); `core/app.py` is the one exception, and it is the composition
# root, wiring sub-apps together lazily at call time, inside method bodies. `core/config.py`
# is the BASE of that stack, not a peer of `app.py` -- `load_config` would need the import
# inside a per-ENTRY validation loop, so `app.py`'s lazy-import pattern does not transfer
# here. Hence the grammar lives in the natural shared home, `core/config.py`, and this
# module imports it rather than the reverse. `tests/test_core_layering.py` is the
# executable guard: a subprocess witness proves importing every `core/` module never
# eagerly drags a sub-app into `sys.modules`, and a static sweep proves no `core/` module
# other than `app.py` names a sub-app at all -- either spelling, lazy or eager.


def validate_posting_paths(owner: str, posting_paths) -> tuple:
    """Return `posting_paths` NORMALISED to a tuple, or raise if it cannot be one.

    It RETURNS rather than merely checking, and the caller stores what it returns, because
    validating a copy while the field keeps the original is its own bug: a one-shot iterable
    (`posting_paths=(p for p in (...))`) validated fine against the materialised copy and was
    then EXHAUSTED on the field. `admits_path` reads a spent generator as truthy, so the
    abstain arm never fires and `any(...)` over nothing rejects EVERY row -- measured, a real
    posting dropped and zero leads. That is the `672ad2a` harm reached through the validator
    written to prevent it. Normalising is preferred to refusing a non-tuple: it accepts the
    list a plugin author may reasonably write, and makes the stored value re-iterable by
    construction rather than by convention.

    FAIL LOUDLY AT CONSTRUCTION, the same posture `lead_layout` takes in `Vault.__init__`
    and for the same reason: every way of getting this field wrong is otherwise SILENT, and
    the two directions fail opposite ways, so neither is self-announcing. Measured, all
    three on the real predicate:

      posting_paths=("/jobs/")   -- a missing comma, so this is a `str`, not a tuple. The
                                    membership loop iterates CHARACTERS, `startswith("/")`
                                    matches every url, and the guard is INERT: #153's
                                    course cards come straight back with nothing red.
      posting_paths=("jobs/",)   -- a missing leading slash. `urlsplit().path` always
                                    begins with "/", so NOTHING matches and 100% of that
                                    board's postings are binned -- the `672ad2a` harm,
                                    aimed by a typo.
      posting_paths=("/jobs",)   -- admits `/jobsearch/...` too. Accepted deliberately (a
                                    prefix is what this field means, and a board may well
                                    file postings at both), but reed's trailing slash IS
                                    load-bearing, so it is called out here rather than left
                                    for someone to rediscover.

    A string is checked BEFORE the iterable check, for the reason `lead_ttl_days` checks
    `bool` before `int`: `str` IS iterable, so an isinstance-iterable test passes it and the
    character-loop bug survives the very guard written to stop it.
    """
    if isinstance(posting_paths, str):
        raise ValueError(
            f"{owner}: posting_paths must be a tuple of path prefixes, not a string -- "
            f"got {posting_paths!r}. A bare string iterates one CHARACTER at a time and "
            f'silently admits every url; write ("{posting_paths}",) with the comma.')
    try:
        prefixes = tuple(posting_paths)
    except TypeError:
        raise ValueError(
            f"{owner}: posting_paths must be a tuple of path prefixes, "
            f"got {type(posting_paths).__name__}") from None
    for prefix in prefixes:
        if not isinstance(prefix, str) or not prefix.startswith("/"):
            raise ValueError(
                f"{owner}: every posting_paths prefix must be a string starting with '/' "
                f"-- got {prefix!r}. Paths are matched against `urlsplit().path`, which "
                f"always begins with '/', so a prefix that does not would reject every "
                f"posting this source returns.")
    return prefixes


def _mk_search(spec, index: int = 0, *, configured: bool = False) -> Search:
    """A searches_spec entry is (label, url) or (label, url, params) - the optional
    params carry per-search metadata (e.g. {"job_type": "perm"}) so the one engine
    covers perm + contract just by varying search terms/params, not code.

    `configured` says which SIDE of `searches_for`'s choice this entry came from; it is
    keyword-only so a positional third argument can never be mistaken for it. `index` is
    this entry's position in whichever list the caller is iterating (`searches_spec` or a
    config override) -- both call sites have a real one via `enumerate`, so passing it
    costs nothing and lets this rung's message be precise rather than always naming
    position 0.

    Validates `spec` via `core/config.py`'s `validate_search_entry` -- see that function's
    docstring for the full three-rung picture and why it lives in `core/` rather than here.
    This is rung 2/3: DEFENCE IN DEPTH for a `spec` that reaches this function WITHOUT going
    through `load_config`'s per-entry check (a test building a spec by hand, a future
    caller) or without going through `BrowserListSource.__post_init__`'s eager check on the
    whole `searches_spec` (a source that duck-types `Source` rather than subclassing it).
    Not redundant with either: this rung is what still catches a malformed entry for every
    caller that never passes through those two."""
    validate_search_entry("a search entry", index, spec)
    label, url = spec[0], spec[1]
    params = spec[2] if len(spec) > 2 else None
    return Search(label=label, url=url, params=params, configured=configured)


def searches_for(source, config=None) -> list:
    """The searches a source should run: a per-source config override
    (`sources.<id>.searches`) if the operator set one, else the source's built-in
    example searches. Config-driving these keeps a user's personal search list out
    of the code. Override entries use the same [label, url, params?] shape as a
    built-in searches_spec entry."""
    if config is not None:
        try:
            override = getattr(config.source(source.id), "searches", None)
        except Exception:
            override = None
        if override:
            return [_mk_search(spec, i, configured=True) for i, spec in enumerate(override)]
    return list(source.searches())


def _demash_company(company: str, location: str) -> str:
    """Some boards (Indeed) render company and location in one DOM node with no
    separator, so the extractor captures e.g. 'Example FoundryPalmerburgh' with location
    'Palmerburgh'. Strip the location suffix ONLY when it is jammed on with no
    separating space (the mashing signature) and something is left; never a
    legitimate trailing token like 'Example Capital ABM'."""
    if location and len(company) > len(location) and company.endswith(location):
        boundary = company[: len(company) - len(location)]
        if boundary and not boundary[-1].isspace():
            return boundary.strip()
    return company


def _sized(value):
    """`value` if it has a length, else an empty list.

    `health_hint` normalises `raw` to a dict, which makes `raw.get(...)` safe and says nothing
    about what comes back. A payload carrying `None` or a scalar under the count key therefore
    raised `TypeError` from `len()` -- inside the very expression written to tolerate a
    malformed payload. Latent: no shipped source emits that shape today.

    `isinstance(list | tuple)`, NOT `hasattr("__len__")`. A STRING has a length, so the
    permissive form counted `{"result": "text"}` as four rows -- swapping a crash for a
    plausible wrong number, which is worse. The value is a list of extracted rows or it is
    not a payload we can count.
    """
    return value if isinstance(value, (list, tuple)) else []


def _first_degraded(rows) -> str | None:
    """The first truthy `degraded` marker among `rows`, or `None`.

    A row-level marker (`_stepstone.py`'s anchor fallback, and `reed.py`'s TWO tiers -- its
    unscoped link cascade and its card-selector fallback) is DIRECT evidence that the good
    path did not run this search, which is why `detect_drift` ranks it above the inferred
    `blank` reason. `_sized` already guarantees `rows` is a list here -- this is the
    row-content half, not the payload normalisation half.

    FIRST marked ROW, not last write: a producer stamping two different markers on the SAME
    row settles that between themselves by assignment order (reed does exactly this, so that
    `card-fallback` names the upstream cause rather than its `link-fallback` symptom), and
    nothing here arbitrates it."""
    for row in _sized(rows):
        if isinstance(row, dict) and row.get("degraded"):
            return row["degraded"]
    return None


def validate_reprobed(qualified: str, value):
    """Raise unless `value` is "" or a real ISO calendar date. Returns it unchanged.

    FAIL LOUDLY AT CONSTRUCTION, the same posture as `validate_posting_paths` beside it: a
    malformed date here is a usage error, and the alternative is a retirement whose recorded
    check date is `2026-99-99` -- which reads as evidence to a human and parses as nothing.
    `date.fromisoformat` is what makes "is this a date" a question with one answer, rather
    than the prose question the guard used to ask.

    FORMAT only. Whether a DISABLED source must carry one, and whether the date is recent
    enough to still be believed, are policy rather than shape, and live in
    tests/test_drifted_boards.py where the floor and `today` are.
    """
    if value == "":
        return value
    if not isinstance(value, str):
        raise ValueError(f"{qualified}: reprobed must be a string ISO date (YYYY-MM-DD) or "
                         f"\"\", got a {type(value).__name__}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{qualified}: reprobed must be an ISO date (YYYY-MM-DD) or \"\", "
                         f"got {value!r}") from None
    return value


def _row_to_lead(source: str, search: Search, row: dict, extra: dict | None) -> Lead:
    """Map an extractor row {title, company?, location?, link, salary?} to a Lead.
    Source-level `extra` sets defaults; the search's own params override them (so
    a perm search on a contract-default source still tags the lead job_type=perm)."""
    location = (row.get("location") or "").strip()
    company = _demash_company((row.get("company") or "").strip(), location)
    lead = Lead(
        source=source,
        search=search.label,
        title=(row.get("title") or "").strip(),
        company=company,
        location=location,
        salary=(row.get("salary") or "").strip(),
        url=row.get("link") or row.get("url") or "",
    )
    for key, value in {**(extra or {}), **(search.params or {})}.items():
        setattr(lead, key, value)
    return lead


@dataclass
class BrowserListSource:
    """A board that renders a scrollable list; one extractor JS returns all rows."""
    id: str
    searches_spec: list           # [(label, url), ...]
    extractor_js: str
    kind: str = "browser"
    enabled: bool = True
    wait: float = 3
    scrolls: int = 2
    scroll_amount: int = 800
    dismiss_js: str | None = None
    extra: dict | None = None
    # JS evaluating truthy when the page is showing its LOGGED-OUT face. Opt-in: a source
    # that declares none reports no auth state, because a source cannot be wrong about a
    # measurement it never took. Only sources whose extractor targets authenticated-only
    # markup need it -- for them, "0 rows" and "logged out" are otherwise indistinguishable,
    # which is what retired linkedin/jobserve/indeed on 2026-08-15.
    auth_probe_js: str | None = None
    # Path prefixes a POSTING url starts with, e.g. ("/jobs/",). Empty = ABSTAIN, which is
    # the shipped default for every source that does not opt in -- see `admits_path`.
    # Declared per source because path shapes are board-specific and a shared default
    # would be one board's convention imposed on twenty-one others.
    posting_paths: tuple = ()
    # Completeness signals this board does not publish AT ALL, so an empty value is the
    # board's answer rather than a broken selector -- e.g. ("company",). Report-only, and the
    # ONLY thing it does is stop `ingest list-sources --health` printing a permanent
    # `UNGUARDED(<field>)` for a source whose rate can never climb (see
    # `HealthStore.unguarded_signals`). It does NOT change what `blank` classifies: the 0.8
    # high-water floor already leaves such a source outside the check, so declaring this
    # cannot suppress a real drift reason, only a standing notice about an impossible one.
    #
    # Empty is the abstaining default, and it is the SAFE direction: an undeclared source that
    # genuinely lacks a field keeps showing the flag, which is noise a human can act on, while
    # a wrongly-declared one goes quiet about a signal that might have been recoverable. So
    # declare it only where the board has been looked at and found not to publish the field --
    # naming a field the extractor simply stopped reading is exactly the mistake this hides.
    unpublished_fields: tuple = ()
    # ISO date (YYYY-MM-DD) on which this source's RETIREMENT was last checked against the
    # live world, or "" for a source that is not retired. #207 ask 4: "a retirement is a claim
    # about the outside world and it goes stale", and the rule for recording that belongs in
    # the source CONTRACT rather than in one test.
    #
    # A declared FIELD rather than a date mined out of the module docstring, and the reason is
    # measured rather than stylistic. The docstring version had to decide, from prose, whether
    # a line asserted that a check HAPPENED -- and every tightening acquired a new hole: a
    # substring test accepted `unverified` (it contains `verified`), and word-bounding it still
    # accepted `not verified`, `never confirmed`, `no longer verified` and `yet to be
    # re-probed`. That set is unbounded because it is a question about natural language, not
    # about a date. A field cannot be negated: it is either a date or it is not.
    #
    # The docstring still carries the REASON, which is the part a human reads and which no
    # field can replace. This carries only the WHEN.
    reprobed: str = ""

    def __post_init__(self) -> None:
        # ASSIGNED back, not merely checked -- see `validate_posting_paths`.
        self.posting_paths = validate_posting_paths(f"source {self.id}", self.posting_paths)
        validate_reprobed(f"source {self.id}", self.reprobed)
        # #212 round 2: the third source-contract declaration, beside the two above.
        # Without this rung, a malformed `searches_spec` still constructs fine -- rungs 1
        # and 2 only run when `load_config` or `.searches()` is actually called -- so the
        # registry's per-plugin isolation ("a broken plugin must not sink the rest",
        # `sources/__init__.py`) never gets a chance to run, because nothing raises at the
        # point a broken plugin is imported. `validate_search_entry` (`core/config.py`) is
        # the same grammar `load_config` and `_mk_search` apply to the other two entry
        # points for a `Search` -- see its docstring for why it lives in `core/` rather
        # than beside `validate_posting_paths`/`validate_reprobed` here.
        if not isinstance(self.searches_spec, (list, tuple)):
            # CodeRabbit #212 round 3: without this, `searches_spec=None` (or any other
            # non-iterable) reached `enumerate(...)` below and raised a raw `TypeError`
            # naming no source at all -- the exact bug class this rung exists to close,
            # one line earlier than the loop that was supposed to close it.
            raise ValueError(
                f"source {self.id}.searches_spec must be a list of `[label, url]`/"
                f"`[label, url, {{params}}]` entries, got a "
                f"{type(self.searches_spec).__name__}")
        for _i, _spec in enumerate(self.searches_spec):
            validate_search_entry(f"source {self.id}.searches_spec", _i, _spec)

    def searches(self) -> list:
        return [_mk_search(spec, i) for i, spec in enumerate(self.searches_spec)]

    def _scroll_step(self, cam, tid) -> None:
        """One scroll step -- one of TWO sanctioned override points for a list-shaped source,
        the other being `parse` (see below). This one is for HOW the page scrolls; `parse` is
        for WHAT a scraped row means.

        Its result is deliberately NOT folded into `errors` below, and that is a judgement
        rather than an oversight. A failed scroll does not produce the unexplained ZERO this
        branch exists to remove: the extractor still runs and reports whatever was visible, so
        the outcome is a LOW count, which `detect_drift` already classifies against the
        source's baseline. Promoting it to `fetch_error` would classify as `unreachable`,
        which is in `_RECOVERABLE` and therefore defers retirement indefinitely -- buying a
        genuinely dead source unlimited time on a benign scroll hiccup, the opposite and
        quieter failure that `_explained`'s docstring warns about. If the tab itself is
        broken, the extractor evaluate errors too and IS recorded.

        A board that virtualizes its results (LinkedIn) must scroll the results PANEL rather
        than the window. That is its sole difference from this class on the SCROLL axis, so
        it is the only thing it gets to change here. Overriding `fetch` wholesale is how the
        LinkedIn subclass silently shipped without the auth probe: the registration declared
        one, so everything READ as covered while the override never evaluated it.

        `parse` is the other axis, for row-level REPAIR rather than scroll mechanics --
        `_NaukrigulfSource.parse` (recovering a company mashed into the title via the listing
        URL's own seam, #151) and `WellfoundSource.parse` (dropping company-profile-card rows
        the extractor's selector lets through, #151) both override it instead of this method,
        PROVIDED they delegate to `super().parse(...)` so `_row_to_lead` and this class's own
        title-non-empty filter still run underneath the repair -- which both do. A `parse`
        override that skips that delegation reimplements row-shaping from scratch and silently
        loses that filter."""
        cam.scroll(tid, self.scroll_amount)

    def fetch(self, ctx: Ctx, search: Search) -> dict:
        cam, sleep = ctx.camofox, getattr(ctx, "sleep", time.sleep)
        tid = cam.create_tab(search.url)
        if not tid:
            return {"result": [], "landed": "", "requested": search.url, "error": "no-tab"}
        # try/finally from the moment the tab EXISTS. `Camofox._api` turns its own failures
        # into `{"error": ...}` rather than raising, but nothing guarantees that of the
        # transport underneath it, of an injected fake, or of `sleep`. And `_run_source`
        # retries on `Exception`, so a raise here does not leak one tab -- it leaks one PER
        # ATTEMPT, and an exhausted Camofox is exactly the outage that retired every source
        # and produced this PR. `core/app.py` already sets the precedent for the doctor probe:
        # "`finally`, not a `close_tab` call repeated on every branch".
        try:
            sleep(self.wait)
            if self.dismiss_js:
                cam.evaluate(tid, self.dismiss_js)
                sleep(0.5)
            for _ in range(self.scrolls):
                self._scroll_step(cam, tid)
                sleep(0.5)
            result = cam.evaluate(tid, self.extractor_js)
            landed = cam.evaluate(tid, "location.href")
            auth_missing, probe_error = self._read_auth_probe(cam, tid)
        finally:
            cam.close_tab(tid)
        rows = result.get("result") if isinstance(result, dict) else None
        # `Camofox._api` captures every failure as {"error": ...} rather than raising, so an
        # evaluate that failed is indistinguishable from one that returned nothing unless we
        # look. Record it: a browser that could not be read is the single clearest explanation
        # for a zero, and discarding it is what let one outage retire every source at once.
        errors = [r.get("error") for r in (result, landed) if isinstance(r, dict) and r.get("error")]
        landed_result = landed.get("result") if isinstance(landed, dict) else None
        # NOT `or search.url` when the evaluate failed: defaulting landed to the requested URL
        # manufactures "no redirect", which is the one signal that would have explained this.
        landed_url = landed_result or ("" if errors else (search.url or ""))
        out = {"result": rows or [], "landed": landed_url, "requested": search.url,
               "auth_missing": auth_missing}
        if errors:
            out["error"] = errors[0]
        if probe_error:
            out["auth_probe_error"] = probe_error
        return out

    def _read_auth_probe(self, cam, tid) -> tuple:
        """`(auth_missing, probe_error)`, evaluated on the SAME tab as the extractor.

        Same tab deliberately: a second fetch could land elsewhere (redirect, A/B split, rate
        limit), and the probe would then describe a different page than the one that yielded
        nothing.

        Only a clean truthy result counts as logged-out. A probe that errored tells us nothing,
        and claiming "logged out" off a broken probe would suppress the retirement of a
        genuinely dead source -- the opposite failure, and a quieter one. But NOT claiming it
        and NOT saying the probe broke are two different decisions: a probe that silently stops
        working (LinkedIn renames a class, a CSP blocks the expression) disables this whole
        guard while every dashboard stays green. So the error comes back too."""
        if not self.auth_probe_js:
            return False, None
        probe = cam.evaluate(tid, self.auth_probe_js)
        if not isinstance(probe, dict) or probe.get("error"):
            err = probe.get("error") if isinstance(probe, dict) else "probe returned a non-dict"
            return False, err
        return bool(probe.get("result")), None

    def parse(self, raw: dict, search: Search) -> list:
        return [
            _row_to_lead(self.id, search, row, self.extra)
            for row in raw.get("result", [])
            if isinstance(row, dict) and (row.get("title") or "").strip()
            and admits_path(self.posting_paths, row.get("link") or row.get("url") or "")
        ]

    def health_hint(self, raw: dict) -> dict:
        # Normalise ONCE. The previous shape guarded the count with `isinstance`, then read
        # `raw.get("landed")` unguarded on the next line, then checked `isinstance` again --
        # so a non-dict `raw` raised `AttributeError` on the host lines and the later guard
        # was unreachable. Three guards that add up to no tolerance at all.
        raw = raw if isinstance(raw, dict) else {}
        hint = {
            # `_sized` not `len(...)` directly: normalising `raw` guarantees a DICT,
            # not that the value under the payload key is sized. `{"result": None}`
            # raised TypeError straight past the tolerance the line above exists for.
            "count": len(_sized(raw.get("result"))),
            "landed_host": _host(raw.get("landed", "")),
            "requested_host": _host(raw.get("requested", "")),
            "landed_path": _path(raw.get("landed", "")),
            "requested_path": _path(raw.get("requested", "")),
            "markers": {},
        }
        # Present only when they actually fired, so `detect_drift` sees keys it can classify
        # on and an ordinary source's signals stay byte-identical to before.
        #
        # `fetch_error` is the load-bearing one: `fetch` has always recorded "no-tab" and this
        # method has always dropped it, so a Camofox outage reached the classifier as an
        # unexplained zero and retired every source at once. A fresh dict built from three
        # keys is exactly how a fourth goes missing.
        if raw.get("error"):
            hint["fetch_error"] = raw["error"]
        if raw.get("auth_missing"):
            hint["auth"] = "missing"
        # Reported but NOT an explanation: a broken probe must not defer retirement (that
        # would keep a genuinely dead source alive), yet it has to be visible or the guard
        # silently disables itself.
        if raw.get("auth_probe_error"):
            hint["auth_probe_error"] = raw["auth_probe_error"]
        # A row the extractor's own fallback stamped is direct evidence of degradation --
        # see `_first_degraded`. Checked on the RAW rows, not the parsed leads: a row-level
        # marker survives even a row `parse` later drops (a blank title), which a signal
        # computed post-parse would miss.
        degraded = _first_degraded(raw.get("result"))
        if degraded:
            hint["degraded"] = degraded
        # How many rows the path guard dropped (#153). Present only when it actually
        # rejected something, so a source that declares no `posting_paths` -- every source
        # but one today -- emits a byte-identical hint and `detect_drift` sees exactly the
        # keys it saw before.
        #
        # This is the half of the guard that keeps it from being a SILENT filter, and it is
        # only half a guard until something READS it: `engine.py` sums it across searches,
        # `detect_drift` classifies `rejected == count` as `paths`, and `_print_report`
        # prints it even below that gate. An earlier revision of this comment claimed the
        # drop would otherwise show up as `count` and the engine's `fetched` diverging --
        # that was FALSE and worth recording rather than quietly deleting: `_run_source`
        # accumulates `fetched` FROM this very `count` (`total += hint["count"]`), so the
        # two are one number and cannot diverge. Without a reader the rejection appeared in
        # neither, which is precisely the silent filter this key exists to prevent.
        rejected = _rejected_path_count(self.posting_paths, raw.get("result"))
        if rejected:
            hint["rejected_paths"] = rejected
        return hint
