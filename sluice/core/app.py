"""The composition root: `Sluice(config)`.

This is the plugin story a registry cannot provide on its own.

**Adapter plugins** are things core CALLS (store, fetcher, renderer, backend). A registry
covers those. **Surface plugins** are things that CALL core -- a web UI, a TUI, a daemon.
A registry does nothing for them. They need a programmatic API to drive. Before this
module existed, sluice had none: its API was `cli.py` functions with the signature
`(args, config)` that constructed their own `Vault()` and `Camofox()`, drove the pipeline
inline, and printed to stderr. A web UI could not drive that, so "a web UI is a plugin"
would have been a lie.

`Sluice` is that programmatic API. It resolves every adapter the config names -- store,
fetcher, renderer, backend (by ROLE: auto/primary/fallback, over the config-selected
provider) -- and it OWNS the pipeline operations as value-returning methods: `ingest(...)`,
`triage(...)`, `compose_cv(...)`, `prep(...)`, `record(...)`, `track(...)`,
`track_confirm(...)`, `track_dismiss(...)`, `normalize_statuses(...)`. It also owns the
state those operations need that is not itself an adapter: the dossier cache
(`dossier_cache`), track's file-backed seen-message set and last-successful-run
watermark, and track's dead-letter store of un-acted-on proposals. `cli.py` is now a thin
shell over this class: each `cmd_*` function builds a `Sluice(config)`, calls one method,
and formats the result for the terminal. A surface (a web UI, a TUI, a daemon) can do the
same without duplicating any of that wiring -- `cli.py` has nothing left worth forking.

Adapters are resolved LAZILY, on first use, and operations that do not need a given
adapter never build one. That preserves the property `cli.py`'s old inside-the-function
imports were protecting: an offline command must never construct a browser, a store, or
an LLM backend just by existing. `sluice triage run --no-llm` still touches no backend;
`sluice ingest list-sources` still touches no vault.
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date

from sluice.core import plugins
from sluice.core import status as _status
from sluice.core.config import Config
from sluice.core.log import get_logger
# Aliased: `dossier_cache` binds a LOCAL `resolve` (the SSRF host resolver), and two
# different `resolve`s one method apart is how a reader mis-attributes a bug.
from sluice.core.paths import resolve as _resolve_path

_log = get_logger("app")


def _today() -> str:
    """The composition root's fallback clock, mirroring `vault.py` and `ingest/sink.py`.
    Module scope so it is patchable: `Sluice.staleness()`'s only other clock source is the
    injected `today` collaborator, which a CLI-layer test has no way to reach."""
    return date.today().isoformat()


def _evidence_failure_reason(exc: BaseException) -> str:
    """Why ONE evidence entry could not be promoted, in words a human can act on.

    NAMED, never an errno. Before per-item isolation existed, a name already taken in
    the citable set reached the terminal as the whole of
    `experience verify: [Errno 17] File exists: <path>` -- which names neither the
    entry that failed nor anything to do about it, and which this repo's "fail loudly,
    but say what to do" posture rules out (#164 review, H2).

    `FileExistsError` before `OSError` because it IS one; `ValueError` carries the
    store's own already-human-worded guard messages (an entry name that is not a bare
    filename component, an unknown kind), so it is passed through rather than
    re-worded into something less specific. The final arm prefers `strerror`
    ("Permission denied") over `str(exc)`, which prepends the `[Errno N]` this
    function exists to keep off the terminal.
    """
    if isinstance(exc, FileExistsError):
        return ("a verified entry of that name already exists -- rename this one, or "
                "delete the inbox copy if it duplicates the verified entry")
    if isinstance(exc, FileNotFoundError):
        return "it is no longer in the inbox -- it was moved or deleted mid-review"
    if isinstance(exc, ValueError):
        return str(exc)
    return getattr(exc, "strerror", None) or str(exc)


@dataclass
class StaleLead:
    """One lead #9 considers stale, as the report sees it. `refused` is set when the lead
    must not be expired at all (today: a #60 sign-off hold); `flagged` carries
    informational markers that do NOT block."""
    slug: str
    ref: object
    status: str
    last_seen: str
    first_seen: str
    days: int
    flagged: list
    refused: str | None = None


# The triage-owned statuses `leads expire` may act on: every TRIAGE_OWNED state except
# `dismiss`, which is already the destination. Application-owned states are absent, so
# they are never even enumerated -- and this same set is handed to update_fields as
# `require_status`, which is what actually holds never-regress when a lead enters the
# application lifecycle mid-sweep.
#
# DERIVED, not hand-written. A literal copy would be an allow-list somebody has to keep
# in step with the vocabulary `core/status.py` owns; deriving it makes the set
# structurally incapable of naming an APPLICATION_OWNED state, which is the property the
# never-regress guard actually needs. `core.status` is pure stdlib, so importing it at
# module scope does not touch cli.py's lazy-import discipline.
#
# See _DISMISSABLE_FROM below (#131) -- dismiss_lead's own required-status set, which
# is NOT a rename of this one: it needs "dismiss" included, since it has no pre-filter
# of already-dismissed leads the way expire_report() has.
#
# Being DERIVED means `unjudgeable` (#169) joined this set automatically, which arrived as
# an accident and is kept as a DECISION -- it is the only bound on a lead that cannot be
# judged. Such a lead is permanently re-selected (`DEFAULT_TRIAGE_STATUSES`), never
# cached, and re-fetched every run, so without a bound it retries a dead posting forever;
# with `--limit` set, an early-sorting block of them can consume the whole budget and
# starve the `new` leads behind them. A posting whose JD has not arrived in `lead_ttl_days`
# is exactly what "stale" is meant to mean, so expiring it is right rather than merely
# convenient. Note the bound is OFF by default, because `lead_ttl_days` is 0 by default --
# the deliberate abstain posture, not an oversight.
_EXPIRABLE = frozenset(_status.TRIAGE_OWNED) - {"dismiss"}

# The denominator of the per-source unjudgeable rate (#169 §2): every lead triage has
# reached a CONCLUSION about, in either direction. `new` is excluded because triage has
# not looked at those leads yet. Derived from TRIAGE_OWNED rather than hand-listed, so a
# seventh triage status joins it automatically -- and deliberately NOT
# DEFAULT_TRIAGE_STATUSES, which is the SELECTION default: a lead leaves that set as soon
# as it is judged, which is what made the printed rate read ~100% for a healthy source.
# See health_report for the measured case and the trade-off taken.
_CONCLUDED = frozenset(_status.TRIAGE_OWNED) - {"new"}

# dismiss_lead's OWN required-status set (#131 decision 6) -- NOT a reuse of
# _EXPIRABLE. _EXPIRABLE excludes "dismiss" safely ONLY because expire_report()
# already filters out already-dismissed leads before expire() ever attempts the write;
# dismiss_lead has no such pre-filter (it resolves one named lead directly, at
# whatever status it is currently at), so excluding "dismiss" here would turn a
# legitimate same-day re-dismiss into a hard CAS refusal instead of the `unchanged`
# outcome decision 5's whole note-tag-idempotency rationale depends on. Both stay
# DERIVED from TRIAGE_OWNED, never hand-listed, so neither can be edited into naming
# an application-owned state -- that property, not which elements are excluded, is
# what actually holds never-regress.
_DISMISSABLE_FROM = frozenset(_status.TRIAGE_OWNED)


@dataclass
class DedupeCluster:
    id: str
    members: list          # list[LeadNote]
    survivor: object       # LeadNote, or None on conflict
    conflict: bool
    flagged_losers: list   # losers carrying a CV/sign-off hold or an application-owned status


@dataclass
class SourceHealth:
    """One source's health, as `job-sluice health` and the MCP `health` tool both
    report it. Mirrors `cmd_health`'s prior inline read -- now the single
    implementation both share (#105)."""
    id: str
    kind: str
    baseline: float
    recent: list        # health.counts(id)
    should_retire: bool
    # Why this source has been producing nothing, and for how many runs -- (None, 0) when it
    # is not stuck. Added because suppressing retirement for an EXPLAINED failure removed the
    # only cumulative signal an operator saw days later: without it a wedged source reads
    # `baseline=0 recent=[0,0,0]` with no RETIRE flag and nothing saying why, which is more
    # mysterious than the wrong answer it replaced. The reason a run failed was already
    # persisted in the health store; nothing ever read it back.
    broken_reason: "str | None" = None
    broken_runs: int = 0
    # #169 §2: FACTS, not a verdict -- this dataclass reports counts, and classifying
    # whether a rate is bad belongs elsewhere (`core/doctor.py`'s `classify_*` shape),
    # the same split the store seam already draws. `unjudgeable` is source X's leads
    # currently at status `unjudgeable`; `concluded` is source X's leads that triage has
    # finished with in either direction (`_CONCLUDED`: every triage-owned status but
    # `new`). The field was called `selected` over DEFAULT_TRIAGE_STATUSES, which is the
    # #156 mistake in a new costume after all -- a lead LEAVES the selection set when it
    # is judged, so numerator and denominator were drawn from populations that diverge
    # over time, and a 99.4%-healthy source printed 100%. The name changed with the set,
    # because "selected" is what made the number readable as something it was not.
    # Both are 0 by default, and stay 0 unless a caller opts into
    # `health_report(include_leads=True)`'s vault walk -- 0/0 must never be read as
    # "measured, clean"; it is indistinguishable from "not measured" by construction,
    # and it is the CALLER's job (cmd_health's `--leads` flag) to know which it asked for.
    unjudgeable: int = 0
    concluded: int = 0


@dataclass
class SignOffResult:
    """Replaces sign_off_cv's former bare (slug, outcome) 2-tuple / None return
    (#131 decision 15). candidates is populated only on outcome == 'ambiguous'."""
    slug: str = ""
    outcome: str = ""   # promoted | discarded | collision | stale | nothing | aborted
                        # | not_found | ambiguous | conflict
    candidates: list = field(default_factory=list)


@dataclass
class DismissResult:
    """#131 decisions 5/6: outcome is one of dismissed | unchanged | refused_status |
    refused_signoff_hold | not_found | ambiguous | conflict. note_appended is True
    ONLY when the write actually committed AND the pre-write snapshot showed the tag
    absent -- neither signal alone distinguishes 'I appended it' from 'it was already
    there' (a plain post-write re-read) or from 'a race loser predicted an append its
    own write never committed' (a plain pre-write snapshot alone)."""
    outcome: str
    slug: str = ""
    status: str = ""            # the FRESH status behind a refusal/unchanged
    candidates: list = field(default_factory=list)
    note_appended: bool = False


@dataclass
class CreateLeadResult:
    """#131 decision 10: outcome is upsert's own six-member vocabulary, passed
    through VERBATIM -- never a bare "created". slug is "" when nothing was
    written (refused | merged_away | merged_away_unproven)."""
    outcome: str
    slug: str = ""


class StoreHasNoLayout(RuntimeError):
    """The configured store has no folder layout, so `leads reconcile` has nothing to do.

    Raised rather than silently reporting an empty sweep: an empty report and "this store does not
    have folders" look identical to a user, and the second is the one that needs saying. The CLI
    turns it into a usage error (rc 2) rather than letting it reach the user as a traceback."""


class StoreCannotRename(RuntimeError):
    """The configured store has no filename-reconciliation pass, so `leads rename` has nothing to
    do. Mirrors `StoreHasNoLayout` exactly, for the identical reason: `reconcile_names` (#151) is
    a vault-only mechanism, not on the Store protocol -- a synthetic-id store has no on-disk
    basename to disagree with a lead's frontmatter, so there is nothing here for it to implement.
    Raised rather than silently reporting an empty sweep, and turned into a usage error (rc 2) by
    the CLI rather than an uncaught traceback."""


_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"
_BACKEND_SEAM = "backend"
# Every seam a constructor override may name. Used to reject a misspelled key at
# construction instead of dropping it silently (see Sluice.__init__).
_SEAMS = (_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM, _BACKEND_SEAM)

# How often the settle loop below re-reads the body, in milliseconds. A constant rather than a
# second config key: the BUDGET is the operator-meaningful number ("how long am I willing to wait
# for a slow board"), while the interval is an implementation detail, and exposing both invites a
# pair that cannot satisfy each other. 250ms is short enough that a stable page's confirming wait
# is not felt and long enough that a slow SPA is not polled dozens of times.
_SETTLE_INTERVAL_MS = 250


def _settle_body(c, tid, budget_ms, sleep=None, guard=None, host=""):
    """Poll `document.body.innerText` until it stops changing, or the budget runs out.

    Returns the LONGEST read seen, not the last, and that is a correctness rule rather than a
    heuristic. A client-rendered page does not only GROW: measured, a posting that had fully
    painted was then overlaid by a cookie banner, so the last read was
    "We use cookies. Accept all. Manage preferences." while the complete JD had already been in
    hand two reads earlier. Returning the last read made the shipped default STRICTLY WORSE than
    `dossier_settle_ms: 0`, which is the opposite of what a settle is for. It is the same
    "prefer whichever source yields more text" rule the caller applies between this body and the
    page's JSON-LD, for the same reason: both candidates are the same page describing itself, so
    taking the longer needs no judgement about what a posting should look like.

    A non-string read is returned IMMEDIATELY and exactly as `evaluate` gave it. Settling must
    not launder a malformed envelope into a string -- the fail-closed BODY_UNREADABLE path is
    what stops a broken browser becoming a cached empty JD -- and a broken probe is a transport
    failure, not a page that has not painted, so polling it cannot help.

    `budget_ms <= 0` reads exactly once and never sleeps, byte-identical to the pre-#228
    behaviour. That is deliberate: it is the setting an operator uses to establish that the
    settle is what changed a result.

    THE BUDGET BOUNDS THE SLEEPING, NOT THE WALL CLOCK, which is worth stating because the
    config key is named in milliseconds. It is spent as `budget_ms // _SETTLE_INTERVAL_MS`
    polls, so elapsed time is that much sleeping PLUS the `evaluate` round trips each poll
    makes -- TWO per re-read, since every read is preceded by a guard call. A budget shorter
    than one interval is rounded UP to a single poll rather than down to none:
    `dossier_settle_ms: 1` asks for a settle, and answering it with the off behaviour would be
    the quiet wrong default this codebase engineers out. A poll count rather than a monotonic
    deadline, because a deadline plus an injected no-op sleep is a busy-wait that would spend
    the budget in real seconds inside an offline test suite.

    Two consecutive EQUAL reads is the stability test, and a blank body never counts as stable
    -- an SPA that has not mounted yields "" (or whitespace) just as reliably as a genuinely
    empty page does, and the two are indistinguishable from here. So a blank body always costs
    the full budget, which is the right way round: the page that needs waiting for is exactly
    the one that looks empty.

    EXHAUSTION IS NOT STABILITY, and is logged rather than returned as if it were. A page still
    painting when the budget runs out yields truncated mid-render text that reads exactly like a
    settled short posting; before #228 it read as "" and was honestly reported as
    `dossier_failed`. Nothing here can tell truncated-but-growing from finished-and-short
    without a judgement about posting length -- which `min_jd_chars` deliberately does not ship
    -- so the fact is recorded instead of guessed at.
    """
    try:
        got = c.evaluate(tid, "document.body.innerText")
    except Exception:
        # `c` is the injected Fetcher seam and may RAISE (a dropped connection, a browser JS
        # error). Converted to the same non-string a malformed envelope yields, so the caller
        # refuses with BODY_UNREADABLE rather than receiving a bare browser exception that
        # never became a DossierUnavailable. `_check_landed` already does this for its own
        # `evaluate`; the body reads did not, and the asymmetry was the bug.
        got = None
    text = got.get("result") if isinstance(got, dict) else None
    if not isinstance(text, str) or budget_ms <= 0:
        return text
    naptime = _SETTLE_INTERVAL_MS / 1000.0
    nap = sleep if sleep is not None else time.sleep
    best = text

    def _read():
        """One guarded body read.

        `guard` re-applies the landed-url check (see `_check_landed`) and RAISES on a refusal,
        so it must run before the read it protects rather than after -- the whole point is that
        the bytes are never pulled from a location policy has not cleared. The caller has
        already checked before the first read, so only the re-reads below go through here,
        keeping the check-to-read window at exactly one `evaluate` for every read: the same
        window this code had before it settled at all.
        """
        if guard is not None:
            # OUTSIDE the try below: a POLICY refusal must propagate, never degrade.
            guard()
        try:
            got = c.evaluate(tid, "document.body.innerText")
        except Exception:
            # Same rule as a malformed envelope: a transport raise is not a page that has not
            # painted, and it must not discard a read already held. Returning a non-string
            # routes it into the loop's existing arms -- keep `best` if one landed, refuse if
            # none did -- instead of escaping `fetch` and losing the dossier outright.
            return None
        return got.get("result") if isinstance(got, dict) else None

    settled = False
    # `max(1, ...)`: a budget shorter than one interval gets a single confirming read rather
    # than none, for the reason the docstring gives.
    for _ in range(max(1, int(budget_ms) // _SETTLE_INTERVAL_MS)):
        stable_candidate = text.strip() != ""
        nap(naptime)
        nxt = _read()
        if not isinstance(nxt, str):
            # A FIRST-read failure has nothing to lose and must still fail closed -- that is
            # what stops a broken browser becoming a cached empty JD, and it is handled above
            # this loop. Here a guarded string read has ALREADY landed, so refusing would
            # discard a JD this fetch is holding, read from a location policy cleared. That is
            # the same "settle is strictly worse than dossier_settle_ms: 0" failure the
            # longest-read rule exists to prevent, arriving by a different route: measured,
            # a good first read followed by one broken envelope returned the full posting at
            # 0 and refused at 5000.
            if not best.strip():
                return nxt
            _log.warning("dossier body probe failed mid-settle host=%s, keeping the longest "
                         "read of %d chars", host or "?", len(best.strip()))
            return best
        if len(nxt.strip()) > len(best.strip()):
            best = nxt
        if stable_candidate and nxt == text:
            settled = True
            break
        text = nxt
    if not settled:
        # Distinguishable in the log from "this board publishes nothing", which is the whole
        # point: a truncated mid-render JD and a genuinely short posting are identical in the
        # returned value and only this line separates them.
        _log.warning("dossier body never settled within %dms host=%s, using the longest of "
                     "%d chars", int(budget_ms), host or "?", len(best.strip()))
    return best


# Read once per successful dossier fetch, alongside document.body.innerText: JobPosting
# structured data, when a board embeds it (#109 tier-2 company resolution).
#
# querySelectorAll, not querySelector: a real job board routinely emits SEVERAL ld+json
# tags, and the page's own JobPosting is often not the first -- a site-wide Organization
# or a BreadcrumbList schema commonly precedes it in DOM order. Taking the first match
# captured the wrong block on exactly the pages tier 2 exists for, so it abstained there
# and nowhere else, which is the hardest shape of this bug to notice.
#
# Each block is parsed in the PAGE and the array re-stringified, rather than concatenating
# raw text: the tags are independent documents, so their raw texts do not compose into one
# parseable JSON value. A block that will not parse becomes `null` rather than discarding
# the whole capture, so one malformed tag cannot cost a good one -- `resolve.py`'s
# `_iter_nodes` skips a null the same way it skips any other non-object. A page with no
# such tag at all yields "[]" (not ""), which that same walk reads as an abstain.
_LD_JSON_JS = (
    "(() => JSON.stringify(Array.from(document.querySelectorAll("
    "'script[type=\"application/ld+json\"]')).map(e => { try { "
    "return JSON.parse(e.textContent); } catch (_) { return null; } })))()"
)

# The injected collaborators of Sluice.__init__ -- NOT seams. Used only to make a
# typo'd keyword point at the right fix: they are keyword-only params, so a typo
# never binds to them and always lands in **overrides, where it would otherwise be
# reported as an unknown SEAM. Pinned to the real signature by a guard test.
# (client/now_iso are Sluice.track() parameters and never reach **overrides.)
_COLLABORATORS = ("sleep", "today", "resolve_host")


# ── track seen/lastrun persistence ───────────────────────────────────────────
# Moved verbatim from cli.py (Task 6): the message-id dedup set and the
# previous-successful-run timestamp are file-backed state Sluice.track() owns,
# not adapters a registry resolves -- there is exactly one implementation and
# no reason for a surface to swap it out.
def _load_seen(path):
    # MISSING -> empty (first run). An UNREADABLE one raises, and the distinction is the
    # whole point: `except OSError` conflated them, so a populated file at mode 000 -- or
    # any I/O error -- returned an empty set while the file sat there, and `_save_seen`
    # then rewrote it FROM the emptied set, compounding the loss. track's dedup store
    # already REFUSES to start when it has been relocated, so shrugging at an unreadable
    # one was incoherent. TWO arms here, not the three `SeenDb.load` has: a line-oriented
    # file has no valid-but-schemaless state to tolerate.
    #
    # The compounding is real but narrower than "any unreadable file": at mode 000 the
    # SAVE fails too, so nothing is rewritten. It bites at mode 0222 (write-only), where
    # the read raises and the save would have succeeded -- measured. `lexists` so a
    # dangling symlink is not mistaken for an absent file and written through.
    if not os.path.lexists(path):
        return set()
    with open(path) as f:
        return set(line.strip() for line in f if line.strip())


def _save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(sorted(seen)))


def _load_lastrun(path):
    """Read the ISO timestamp of the previous successful (non-dry-run) track run,
    so the next run's Gmail query can be scoped since then (F10) instead of the
    fixed lookback window. Missing/unreadable file just means "no prior run".

    Swallowing the error is right here, unlike the dedup loaders beside it: nothing is
    destroyed, and `_save_lastrun` repairs the file with a correct value on the next
    successful run. But "no prior run" is not free. The fallback is
    `gmail_lookback_days` (default 2), and `_gmail_query` uses `since_iso` INSTEAD of
    that window rather than widening to whichever is larger -- so on an install idle
    longer than the lookback, the fallback window is NARROWER than the real gap, and a
    receipt that landed in it is never queried again. The lead then sits in `applied`
    indefinitely. Losing this file is cheap; losing it after a long gap is not."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _save_lastrun(path, iso):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(iso)

# ── backend construction ─────────────────────────────────────────────────────
# Per-token providers authenticate with an API key and take an optional base_url
# override, both from the environment. claude-max is deliberately absent: it
# shells the flat-rate CLI and needs no credentials.
_PROVIDER_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
}


def _provider_creds(name):
    """(api_key, base_url) for a backend name, from the environment. An unset
    *_BASE_URL yields "", which make_backend reads as "use the provider default"."""
    key_var, url_var = _PROVIDER_ENV.get(name, ("", ""))
    if not key_var:
        return "", ""  # claude-max: flat-rate CLI, no credentials to resolve
    return os.environ.get(key_var, ""), os.environ.get(url_var, "")


def _make_primary(name, model, *, effort, host, claude_path, timeout=None):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    # timeout=None means "the caller expressed no preference", and make_backend coalesces
    # it to the shipped default -- so a sub-app that has no timeout knob keeps exactly the
    # behaviour it had, while cv can pass cv.compose_timeout through (#28).
    return make_backend(name, model, api_key=api_key, base_url=base_url,
                        effort=effort, claude_host=host, claude_path=claude_path,
                        timeout=timeout)


def _make_fallback(name, model, *, host, claude_path, timeout=None):
    """Build the fallback leg, or None when its credentials are absent.

    A missing key is not fatal: running primary-only (a claude-max setup with no
    per-token key configured) is legitimate and must keep working. But it *is* a
    degraded state -- the run has no safety net if the primary dies -- so warn
    loudly at build time rather than letting it surface as a 401 at the exact
    moment the primary goes down. When the fallback is explicitly *selected*
    (`--backend fallback`) there is nothing to degrade to, so make_backend's
    missing-key error is allowed to propagate; see Sluice.backend.

    `host`/`claude_path` (#117): claude-max is a legitimate name for EITHER role, and
    there is one `claude_max_host`/`claude_max_path` pair per sub-app config regardless
    of which role it plays -- so Sluice.backend() threads the same values it gives
    _make_primary through here too. Harmless for every other provider: make_backend
    passes claude_host/claude_path to every factory, and only ClaudeMaxBackend reads
    them (see _make_primary, which already does this unconditionally)."""
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    if name in _PROVIDER_ENV and not api_key:
        _log.warning(
            "fallback backend '%s' has no API key (%s unset): running with no "
            "fallback -- a primary failure will now fail the run",
            name, _PROVIDER_ENV[name][0])
        return None
    return make_backend(name, model, api_key=api_key, base_url=base_url, timeout=timeout,
                        claude_host=host, claude_path=claude_path)


def _make_fallback_strict(name, model, *, host, claude_path, timeout=None):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    return make_backend(name, model, api_key=api_key, base_url=base_url, timeout=timeout,
                        claude_host=host, claude_path=claude_path)


class Sluice:
    """Resolve the configured adapters and expose the pipeline operations.

    `overrides` lets a caller (a test, or a surface with its own wiring) inject a
    pre-built adapter and skip the registry. It is the seam's test seam.
    """

    # `--backend` names a ROLE, not a provider: which of the two configured backends
    # to use. The old provider-flavoured values stay as aliases so existing crons and
    # muscle memory keep working now that selection is config-driven.
    _BACKEND_ROLES = ("auto", "primary", "fallback")
    _BACKEND_ALIASES = {"claude-max": "primary", "deepseek": "fallback"}

    # `sleep` and `today` are explicit keyword-only params rather than members of
    # **overrides: they are injected VALUES, not adapters resolved by name, and the
    # seam-key validation below would (correctly) reject them.
    def __init__(self, config=None, *, sleep=None, today=None, resolve_host=None,
                 **overrides):
        # A composition root with no config uses the code defaults, exactly as the
        # adapters did when cli.py constructed them bare. Callers (and tests) that pass
        # None must get a working Sluice, not an AttributeError deep inside a factory.
        self.config = config if config is not None else Config()
        # Fail loudly on a misspelled seam rather than accepting the key and then never
        # using it -- the same quiet-wrong-default class make_backend's unknown-provider
        # raise exists to remove. The live trap was `fetch`: ARCHITECTURE.md labelled the
        # seam that while the config key is `fetcher`, so the plausible typo was the
        # documented word. Validating here is what made that mismatch worth fixing.
        unknown = sorted(set(overrides) - set(_SEAMS))
        if unknown:
            # Reuses the seam-resolution error so the message shape matches what
            # `plugins.get` raises for an unknown adapter NAME: same failure class,
            # one level up (an unknown seam rather than an unknown name within one).
            raise plugins.UnknownAdapter(
                "seam override", unknown[0], _SEAMS,
                hint=(f"injected collaborators ({', '.join(_COLLABORATORS)}) are "
                      f"keyword-only parameters, not seam overrides"))
        self._overrides = {k: v for k, v in overrides.items() if v is not None}
        self._sleep = sleep
        self._today = today
        # DNS for the dossier url guard (#18). None means urlguard's real resolver;
        # tests inject a fake so the suite never resolves. A collaborator, not a
        # seam: a registry entry is reachable from config, so a seam-resolved
        # resolver would put an off switch for the SSRF guard under a YAML key.
        self._resolve_host = resolve_host
        # Cached per seam for the process's WHOLE lifetime (see _resolve) -- correct only
        # because every adapter factory _resolve can currently reach (vault, camofox) has
        # no construction-time side effects. A one-shot CLI invocation never exercised
        # that fact; a long-lived caller (`mcp serve`, sluice/mcpserver.py) depends on it.
        # A future adapter factory with a construction-time side effect must either stay
        # free of one or revisit this cache.
        self._cache: dict = {}

    # ── adapter resolution ───────────────────────────────────────────────────
    def _resolve(self, seam: str, name: str, cfg):
        if seam in self._overrides:
            return self._overrides[seam]
        if seam not in self._cache:
            # Import the plugin package so its members self-register. Done here rather
            # than at module scope so that merely importing `core.app` stays free of
            # browser/vault/backend imports.
            _import_plugins(seam)
            factory = plugins.get(seam, name)   # raises UnknownAdapter, listing valid names
            self._cache[seam] = factory(cfg)
        return self._cache[seam]

    def store(self):
        """The configured Store. Defaults to `vault`, today's only implementation."""
        return self._resolve(_STORE_SEAM, getattr(self.config, "store", "vault"), self.config)

    def fetcher(self):
        """The configured Fetcher. Constructed on first use, so an offline command that
        never fetches never opens a browser."""
        return self._resolve(_FETCHER_SEAM, getattr(self.config, "fetcher", "camofox"),
                             self.config)

    def renderer(self, cvcfg):
        """The configured Renderer. Takes the cv config because that is where its knobs
        live (`cv.renderer`, `cv.render_script`, `cv.template`, ...)."""
        # The getattr default is unreachable for a real CvConfig (the field always has a
        # value), but it is still the name a caller sees if that ever changes -- and a
        # quiet WRONG default is precisely the bug class this codebase engineers out
        # (see CLAUDE.md's "Fail loudly at construction"). `cv.renderer` defaults to
        # `template` now, not `script`; this fallback must track that or a future
        # regression here would fail silently into the retired norm instead of the
        # current one.
        return self._resolve(_RENDERER_SEAM, getattr(cvcfg, "renderer", "template"), cvcfg)

    def backend(self, role, *, primary_name, primary_model, effort, host, claude_path,
                fallback_name, fallback_model, timeout=None):
        """cli.py's old _select_backend, moved verbatim in behaviour. auto degrades to
        bare primary (with a warning) when the fallback has no creds; fallback is strict.
        make_backend stays the provider factory -- an unknown provider name raises
        BackendError there, unchanged.

        Unlike store/fetcher/renderer this is not cached on self: each sub-app's config
        supplies different primary/fallback fields (triage's medium effort vs cv's max,
        for instance), so there is no single per-Sluice "the" backend to memoize."""
        from sluice.core.backends import BackendError, FallbackBackend
        role = self._BACKEND_ALIASES.get(role, role or "auto")
        # argparse guards the CLI, but this method is called directly too. Without
        # this an unrecognised choice ("primry") would match neither branch below and
        # land silently in `auto` -- the same quiet-wrong-default this method exists to
        # remove, and the opposite of make_backend's fail-at-construction rule.
        if role not in self._BACKEND_ROLES:
            raise BackendError(
                f"unknown backend choice '{role}' (expected "
                f"{', '.join([*self._BACKEND_ROLES, *self._BACKEND_ALIASES])})")
        # A constructor override wins -- but only AFTER the role guard above. Checking
        # first would make `Sluice(cfg, backend=X).backend("primry", ...)` return X
        # instead of raising, reinstating the exact quiet-wrong-default the guard exists
        # to remove. Unlike store/fetcher/renderer this cannot go through `_resolve`:
        # that memoizes per seam, and `backend()` is deliberately uncached because each
        # sub-app passes different construction params (see the docstring above).
        if _BACKEND_SEAM in self._overrides:
            return self._overrides[_BACKEND_SEAM]
        if role == "fallback":
            # Explicitly asked for it, so a missing key is fatal, not degradable.
            return _make_fallback_strict(fallback_name, fallback_model, host=host,
                                         claude_path=claude_path, timeout=timeout)
        primary = _make_primary(primary_name, primary_model, effort=effort, host=host,
                                claude_path=claude_path, timeout=timeout)
        if role == "primary":
            return primary
        fallback = _make_fallback(fallback_name, fallback_model, host=host,
                                  claude_path=claude_path, timeout=timeout)
        return FallbackBackend(primary, fallback) if fallback else primary

    def staleness(self, *, include_stale: bool = False):
        """The #9 lead-age rule for one invocation. Built HERE, once, so `leads expire`,
        cv and apply cannot disagree about what "stale" means.

        Composition-root state like `dossier_cache`, not an adapter seam, so it does not
        go through `_resolve`: there is nothing to select by name.

        `self._today` is a zero-arg CALLABLE, not a string -- VaultSink does
        `today or _today` and then calls it, and every test injects `lambda: "..."`. It
        must be CALLED here. Binding the function into the frozen policy would reach
        date.fromisoformat(<function>) -> TypeError, which the policy's ValueError guard
        does NOT catch, turning the designed fail-safe abstain into an unhandled
        traceback on `cv run`, `apply prep` and `leads expire`. StalenessPolicy refuses a
        non-str at construction so that mistake cannot reach a gate silently.
        """
        from sluice.core.leads import StalenessPolicy
        clock = self._today or _today
        return StalenessPolicy(ttl_days=self.config.lead_ttl_days,
                               today=clock(),
                               include_stale=include_stale)

    def _dossier_dir(self) -> str:
        """The ONE dossier cache directory, for both triage and cv (#80).

        It was two sub-app keys (`triage.dossier_dir`, `cv.dossier_dir`) that happened
        to carry the same `./dossiers` literal, so the cache the two sub-apps share was
        shared only by coincidence of that default. Moving one and not the other splits
        it, and cv then re-fetches every dossier over the live SSRF-guarded network
        path -- which is why one root key, resolved in one place, is the fix rather
        than two keys and a test that they match.

        Resolved HERE and not in `load_config`, for the same reason `vault_dir` is
        resolved in the store factory: the value reaches this class through a ROOT
        Config a caller can build by hand -- `Sluice(Config())`, which every test does --
        so a blank left unresolved would write the cache into the cwd. The sub-app paths
        (`seen_db`, `token_path`, `audit_jsonl`) resolve inside their LOADERS instead,
        because nothing constructs a `TrackConfig`/`TriageConfig` by hand and hands it to
        a command: the loader is their only entry point. Two placements, one rule --
        resolve at whichever boundary every caller has to pass through.
        """
        return _resolve_path(env_var="DOSSIER_DIR",
                             config_value=getattr(self.config, "dossier_dir", ""),
                             kind="cache", name="dossiers")

    def dossier_cache(self, dossier_dir, ttl_days, min_jd_chars):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text read via
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses.

        The lead url comes off a scraped listing, so it is guarded (#18): checked before
        a tab is opened, and the LANDED url re-checked before the body is read. A refusal
        RAISES rather than returning an empty dossier -- see the comment on the raise.

        Also captures document.title and any JSON-LD script tag's text content in the
        same already-open tab (#109), for triage's tier-2 company resolution. Both are
        best-effort: an unreadable probe degrades to "" rather than refusing the fetch.

        `min_jd_chars` (#169) is the ROOT config value, not a literal: both call sites
        pass `self.config.min_jd_chars` so triage and cv -- which share this one cache
        directory (#80, see `_dossier_dir` above) -- always agree on the floor below
        which a fetched JD is treated as not having arrived.

        `dossier_settle_ms` (#228) is read off `self.config` inside the closure instead of
        being a parameter, and the asymmetry is deliberate rather than an oversight: it
        governs THIS fetch's transport and has a single consumer, while `min_jd_chars` is a
        cache-ADMISSION floor two sub-apps sharing one directory must agree on. A caller
        that wanted a different settle would be tuning a browser, not a shared store.
        """
        from sluice.core.dossier import DossierCache, jd_from_structured_data
        from sluice.core import urlguard
        # Parsed once per cache, not per fetch. Raises here if a Config was built by
        # hand with a malformed list (load_config validates the same way).
        allow = urlguard.parse_allow_hosts(
            getattr(self.config, "dossier_allow_hosts", []))
        # `or` the module default: self._resolve_host is None unless a test injects one.
        resolve = self._resolve_host or urlguard._resolve
        cam = {}

        def _refuse(reason, host=""):
            """Log and RAISE. Never returns.

            It raises rather than returning the exception for the caller to raise:
            with a returning helper, dropping one `raise` keyword downgrades a
            refusal to a logged warning followed by a fall-through that reads and
            returns the body. That one-token deletion is precisely Task 9's own
            mutant, so the shape must make it impossible rather than merely tested.

            Three of the eight slugs (NO_TAB, LANDED_UNREADABLE, BODY_UNREADABLE) mean
            Camofox itself failed -- `_api` swallows a timeout or connection error into
            the same `{"error": ...}` shape a policy refusal reaches this closure through
            (core/camofox.py). Logging those as "refused" points an operator at an
            allowlist that cannot fix a dead browser server, so they get their own word
            and their own (still-a-DossierBlocked) exception type -- see
            urlguard.DossierUnavailable.
            """
            transport = reason in urlguard._TRANSPORT_REASONS
            _log.warning("dossier fetch %s (%s) host=%s",
                         "failed" if transport else "refused", reason, host or "?")
            # The exception carries the SLUG ONLY: cv/engine.py logs str(e) verbatim
            # and triage/engine.py stores it in report.failures.
            cls = urlguard.DossierUnavailable if transport else urlguard.DossierBlocked
            raise cls(reason)

        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
            page_title, structured_data = "", ""
            landed_url = ""
            if url:
                pre = urlguard.check_url(url, allow_hosts=allow, resolve=resolve)
                if not pre.allowed:
                    _refuse(pre.reason, pre.host)
                if "client" not in cam:
                    cam["client"] = self.fetcher()
                c = cam["client"]
                tid = c.create_tab(url)
                if not tid:
                    # PRE-EXISTING behaviour was to fall through and return the empty
                    # dossier shape here. That is the outcome this whole feature exists
                    # to prevent: get_or_build CACHES it for ttl_days, triage judges the
                    # lead on a JD nobody read, apply_verdict writes a status from it,
                    # and report.failures stays empty. Raising costs one retry next run.
                    # OUTSIDE the try below on purpose: a falsy tid means no tab was
                    # ever opened, so there is nothing for close_tab to close.
                    _refuse(urlguard.NO_TAB, pre.host)
                # Every exit past this point -- a refusal or a clean read -- must close
                # the tab it opened. A bare Camofox never raises (`_api` swallows into
                # `{"error": ...}`), but `c` is the injected Fetcher seam, and a future
                # non-Camofox implementation that DOES raise must not leak a tab; hence
                # `finally`, not a `close_tab` call repeated on every refusal branch.
                try:
                    # Camofox's navigate awaits page.goto(waitUntil='domcontentloaded'),
                    # so the tab HAS navigated by now and HTTP redirects are already
                    # followed. The checks below assert that rather than trusting it.
                    nonlocal_landed = [""]

                    def _check_landed():
                        """Re-derive the tab's CURRENT url and re-apply the guard. Raises.

                        A closure rather than a straight-line block because #228's settle
                        reads the body more than once, and every one of those reads must be
                        preceded by a fresh check. Between a check and a read the page can
                        move under us -- a meta refresh, a JS `location =`, a late
                        client-side route -- and a client-rendered posting, which is exactly
                        what the settle exists for, is the kind of page most likely to do it.
                        Checking once and then reading for up to `dossier_settle_ms` would
                        turn this guard's one-evaluate window into a multi-second one, and
                        the body read after such a move comes from the NEW location: an SSRF
                        past the check that #18 added, widened by the feature above.
                        """
                        # `c` is the injected Fetcher seam and is free to RAISE -- a browser
                        # JS error, a timeout, a dropped connection. Left unwrapped, that
                        # escaped `fetch` entirely as a bare RuntimeError and discarded a JD
                        # already read from this tab, which is the harm `_probe`'s own
                        # isolation exists to prevent. Converted to the slug that already
                        # names this condition, so callers get the DossierUnavailable they
                        # understand instead of an exception class from the browser layer.
                        # A POLICY refusal below still propagates untouched: that is the
                        # whole point of running this outside `_probe`'s try.
                        try:
                            res = c.evaluate(tid, "location.href")
                        except Exception:
                            _refuse(urlguard.LANDED_UNREADABLE, pre.host)
                        landed = res.get("result") if isinstance(res, dict) else None
                        if not isinstance(landed, str):
                            _refuse(urlguard.LANDED_UNREADABLE, pre.host)
                        if not landed or landed == "about:blank":
                            _refuse(urlguard.NOT_SETTLED, pre.host)
                        post = urlguard.check_url(landed, allow_hosts=allow, resolve=resolve)
                        if not post.allowed:
                            _refuse(urlguard.LANDED_BLOCKED, post.host)
                        # Captured for the JSON-LD recovery below, which drops any node whose
                        # own url names a DIFFERENT posting. Assigned on every check, so it
                        # holds where the tab actually ended up.
                        nonlocal_landed[0] = landed

                    _check_landed()
                    # Only now is the body safe to pull into memory.
                    #
                    # SETTLE FIRST (#228). `create_tab` awaits page.goto with
                    # waitUntil='domcontentloaded', which fires when the HTML document is
                    # parsed -- a single-page app has not mounted, fetched or painted the
                    # posting by then. Read immediately, two live ATS vendors returned an
                    # empty body on EVERY lead, and `cv run` composed from the bundle with
                    # no job description at all: a plausible PDF, violations=0, tailored to
                    # nothing. The partial render is worse, because it is not even flagged --
                    # chrome without the posting is a SHORT NON-EMPTY body, which `jd_arrived`
                    # accepts at the shipped `min_jd_chars` of 0.
                    #
                    # Polls `evaluate` and NOTHING ELSE, so the four-member Fetcher Protocol is
                    # untouched: no new seam member for an implementation to be missing, no
                    # change to what tests/harness/browser.py must stand in for. Stops as soon
                    # as two consecutive reads agree, so a page that was already complete pays
                    # one extra evaluate and one interval -- not the whole budget, which would
                    # be added to every dossier fetch in a run.
                    md = _settle_body(c, tid, self.config.dossier_settle_ms,
                                      self._sleep, guard=_check_landed, host=pre.host)
                    if not isinstance(md, str):
                        # Same reasoning as no-tab: a non-string body used to become a
                        # cached empty JD indistinguishable from a real empty one.
                        _refuse(urlguard.BODY_UNREADABLE, pre.host)
                    # #109 tier-2 resolution. BEST-EFFORT ABOUT CONTENT, NOT ABOUT
                    # LOCATION, and the two halves are now different: a source that omits a
                    # page title or JSON-LD is common and not a transport failure, so a
                    # missing, malformed or raising probe still degrades to "" rather than
                    # refusing an otherwise-good fetch -- but the landed-url check that runs
                    # before each probe REFUSES, and that refusal propagates. `page_title`
                    # inherits that guard even though it is genuinely resolution-only,
                    # because both probes read from the same tab: one shared check is
                    # cheaper than reasoning per-probe about which reads may cross a move.
                    def _probe(label: str, js: str) -> str:
                        """One metadata probe: best-effort about its RESULT, guarded on
                        its LOCATION.

                        A malformed RESULT SHAPE was the only degradation the first
                        draft handled, which made the promise above true of exactly
                        half the failure modes: `c` is the injected Fetcher seam, so
                        `evaluate` is free to RAISE (a browser JS error, a timeout, a
                        dropped connection). An unwrapped raise propagates out of
                        `fetch` entirely and discards the JD body ALREADY read from
                        this tab -- the whole dossier lost over a field nothing is
                        required to have. Each probe is wrapped on its own so one
                        raising cannot blank the other.

                        The landed-url guard runs OUTSIDE this try, deliberately, so a
                        policy refusal propagates rather than degrading to "". Its own
                        `evaluate` is wrapped at the call site for the reason above, so a
                        transport failure there is a DossierUnavailable rather than a bare
                        browser exception escaping `fetch`.
                        """
                        # GUARDED, and outside the try below so a refusal PROPAGATES rather
                        # than degrading to "". This probe stopped being best-effort metadata
                        # the moment `structured_data` became a JD source: a tab that moves
                        # after the settle's last check would otherwise have its JSON-LD read
                        # from the new location and that content returned AS THE JD, with no
                        # refusal anywhere -- the same TOCTOU the body reads were just fixed
                        # for, in the probe that was promoted into the JD path.
                        _check_landed()
                        try:
                            res = c.evaluate(tid, js)
                        except Exception:
                            # Degrading, but not silently: tier-2 abstaining on every
                            # lead looks identical whether the pages carry no metadata
                            # or the probe never completed, and only one of those is
                            # an operator's problem. Worded to avoid "failed" and
                            # "refused" -- _refuse owns that pair, and two tests read
                            # the log for exactly that contrast.
                            _log.warning("dossier probe errored (%s) host=%s, degrading to blank",
                                         label, pre.host or "?")
                            return ""
                        got = res.get("result") if isinstance(res, dict) else None
                        return got if isinstance(got, str) else ""

                    page_title = _probe("page_title", "document.title")
                    structured_data = _probe("structured_data", _LD_JSON_JS)
                    landed_url = nonlocal_landed[0]
                finally:
                    c.close_tab(tid)
            # PREFER WHICHEVER SOURCE YIELDS MORE TEXT (#228). The settled body is the primary
            # JD; the page's own JSON-LD `JobPosting.description` is the fallback, and it is
            # already in hand from the tier-2 probe above, so consulting it costs nothing.
            #
            # A LENGTH comparison, deliberately -- not a threshold, not a ratio, and not a list
            # of client-rendered hosts. All three would be judgements about what a real posting
            # looks like, which is exactly the judgement `min_jd_chars` refuses to ship
            # uninvited (its default is 0 for that reason). Length is not such a judgement:
            # both candidates are the same page's own description of itself, so taking the
            # longer needs no opinion about jobs -- and no host list to go stale as vendors
            # change their rendering.
            #
            # Measured on live postings from both ATS vendors in #228, one each way: one
            # settles to a short run of navigation chrome while its JSON-LD carries the real
            # posting, so without this the settle above would have converted an honest
            # empty-and-flagged failure into a silent one -- the very case #228 was filed
            # about. The other settles to the real posting and KEEPS it, its own JSON-LD being
            # the same text as markup and no longer. Both directions are exercised in
            # tests/test_dossier_structured_jd.py, which carries the figures.
            ld_jd = jd_from_structured_data(structured_data, landed_url=landed_url, log=_log)
            if len(ld_jd) > len(md or ""):
                # Named when it happens: without this line a JD substituted from metadata is
                # indistinguishable in the cache from one the page rendered, so a wrong
                # substitution leaves no trace for an operator to find.
                _log.info("dossier JD taken from JSON-LD (%d chars) over the rendered body "
                          "(%d chars) host=%s", len(ld_jd), len(md or ""), pre.host or "?")
                md = ld_jd
            return {"jd": {"markdown": md or ""}, "glassdoor": {},
                    "page_title": page_title, "structured_data": structured_data}

        return DossierCache(dossier_dir, ttl_days, fetcher=fetch, min_jd_chars=min_jd_chars)

    def ingest(self, sources, *, dry_run=False, json_sink=False, out=None):
        """Run the ingest sub-app: fetch each given source, dedup/relevance-gate
        through `ingest.engine.run`, and write survivors to a sink. Which sources
        to run (the enabled/disabled overlay, `--source`/`--all`) stays in
        cli.py's `_selected` -- this method just executes the list it is handed.

        `dry_run` OR `json_sink` both route to `JsonSink`, never `VaultSink`: a
        dry run skips the vault and the dedup-state WRITE, and `--sink json` is
        an explicit request to skip the vault -- so neither constructs the store.
        It is not disk-free, and saying so was wrong in three other places before
        this one: `_update_health` runs on every source on every run, so a dry
        run still records health and can create `sluice_health.json`. That is
        deliberate -- health is a fact about the FETCH, which a dry run really
        performs -- but it is a write, and this comment used to deny it.
        `SeenDb` IS constructed on both branches, and read: a dry run that lied
        about what had already been seen would be useless. What those branches
        skip is the dedup-state WRITE, not the read (see the comment on `seen`
        below, and `_ingest_run`'s sink). `out` lets a caller (a future surface, or a test) capture
        the JSON lines somewhere other than stdout; it defaults to stdout because
        that is what `sluice ingest run --dry-run`/`--sink json` has always done."""
        import sys
        from sluice.core.health import HealthStore
        from sluice.core.seendb import SeenDb
        from sluice.ingest.base import Ctx
        from sluice.ingest.engine import run as _ingest_run
        from sluice.ingest.sink import JsonSink, VaultSink

        # `Ctx.sleep` and `VaultSink(today=)` are injection points those types already
        # declare; this root simply never passed them. Without the first, a shipped
        # BrowserListSource costs its real page-settle wait per search (measured: 5.0s
        # for one remoteok search, against a whole suite that runs in ~1.2s). Without
        # the second, nothing above the sink can move the clock, so date-dependent
        # behaviour -- `last_seen` monotonicity especially -- is untestable from here.
        # Resolved FIRST, before any adapter is built: refusing after a browser has
        # been started is wasted work, and the whole point is to stop before touching
        # anything.
        #
        # `fatal=not (dry_run or json_sink)` and NOT a check placed after the dry-run
        # branch below -- there is no such position. `seen` is constructed here and
        # reaches the engine on BOTH sides of that branch, correctly: a dry run that
        # lied about what had already been seen would be useless. So the refusal
        # decision is made at construction, from the same two flags the sink choice
        # uses. A dry run and a --sink json run write no dedup state and have nothing
        # to lose; a real run that silently starts from an EMPTY dedup set re-submits
        # every already-known lead to the write path. `Vault.upsert` now probes
        # `_merged/` by name (#81) before creating, so a merged-away lead usually
        # self-heals instead of being re-created -- but the probe is name-keyed, so one
        # whose title has drifted past every candidate still slips through, and that
        # can mean a second application under their name, reported as ordinary
        # `created: N` activity.
        seen = SeenDb(_resolve_path(env_var="SEEN_DB", config_value="", kind="state",
                                    name="seen.db",
                                    fatal=not (dry_run or json_sink)))
        ctx = Ctx(camofox=self.fetcher(), config=self.config, sleep=self._sleep)
        health = HealthStore()  # default path lives in HealthStore.__init__ (SLUICE_HEALTH)
        if dry_run or json_sink:
            sink = JsonSink(out or sys.stdout)
        else:
            sink = VaultSink(self.store(), seen, today=self._today)
        return _ingest_run(sources, ctx, sink, seen, health)

    def normalize_statuses(self, *, dry_run=False) -> dict:
        """Canonicalize every lead note's status field -- fix value drift and
        quoting, collapse duplicate status lines -- via the store. Thin
        passthrough to `Store.normalize_all_statuses`; the CLI just formats the
        returned summary dict for `sluice triage normalize-status`."""
        return self.store().normalize_all_statuses(dry_run=dry_run)

    def _dedupe_report(self, store):
        from sluice.core.leads import cluster_duplicates, cluster_id, pick_survivor
        from sluice.core.status import resolve_merge_status, is_application_owned
        clusters = cluster_duplicates(
            store.read_leads(),
            title_noise=self.config.dedupe_title_noise_words,
            location_noise=self.config.location_noise_words)
        out = []
        for members in clusters:
            winner, outcome = resolve_merge_status([n.status for n in members])
            survivor = pick_survivor(members, winner) if outcome == "ok" else None
            flagged = [n for n in members if n is not survivor and (
                n.fm.get("tailored_cv") or n.fm.get("needs_signoff")
                or n.fm.get("pending_cv") or is_application_owned(n.status))]
            out.append(DedupeCluster(id=cluster_id(members), members=members,
                                     survivor=survivor, conflict=(outcome != "ok"),
                                     flagged_losers=flagged))
        return out

    def expire_report(self, policy=None):
        """The #9 staleness REPORT: leads whose `last_seen` is older than
        `lead_ttl_days`, in a triage-owned status. Changes nothing.

        Returns [] when the knob is unset -- the caller distinguishes that from "nothing
        is stale", because printing `0 stale` for an install that never configured the
        feature would let a user believe a knob they never set is protecting them.

        `policy` is injectable so `expire()` can build exactly ONE per invocation, the way
        `_dedupe_report(store)` takes its store. Calling `self.staleness()` in both places
        reads the production clock twice, and a sweep crossing midnight would then stamp
        `[expire <one date>]` on a note whose "Nd old" figure was computed against the
        other -- an audit line disagreeing with itself.
        """
        policy = policy or self.staleness()
        if policy.ttl_days <= 0:
            return []
        out = []
        for n in self.store().read_leads(_EXPIRABLE):
            last_seen = n.fm.get("last_seen", "")
            if not policy.is_stale(last_seen):
                continue
            # `tailored_cv` and `needs_signoff` are INFORMATIONAL. Only `pending_cv`
            # refuses, because dismissing that lead silently discards work IN FLIGHT: a
            # composed CV a human has not yet signed off on, with no signal that it went.
            # (This refusal once had a second, stronger reason -- sign_off_cv resolved via
            # read_leads({"shortlist"}), so a dismissed lead became unreachable. That is no
            # longer true: sign_off_cv now resolves over all of TRIAGE_OWNED, pinned by the
            # `dismiss` case of test_a_held_lead_can_be_discharged_from_any_triage_status.
            # The refusal survives on the discard-work reason alone.)
            # A note carrying needs_signoff ALONE must NOT be refused: Vault.sign_off
            # no-ops without pending_cv, so the refusal message's own escape hatch would
            # do nothing and the lead would be stuck forever.
            flagged = []
            if n.fm.get("tailored_cv"):
                flagged.append("cv")
            if n.fm.get("needs_signoff"):
                flagged.append("signoff-flag")
            out.append(StaleLead(
                slug=n.slug, ref=n.ref, status=n.status, last_seen=last_seen,
                first_seen=n.fm.get("first_seen", ""), days=policy.days(last_seen),
                flagged=flagged,
                refused="sign-off-hold" if n.fm.get("pending_cv") else None))
        return out

    def expire(self, slugs=None):
        """Dismiss stale leads. `slugs` empty/None expires everything the report lists;
        a non-empty list narrows to EXACT slug matches.

        Returns [(slug, outcome)] with outcome one of: 'dismissed'; 'refused-signoff'
        (a #60 hold, see expire_report); 'no-match' (a named slug that is not in the
        stale set -- narrowing is not a licence to dismiss an arbitrary lead by name);
        'ambiguous' (two or more stale notes claim that slug, so which lead was named is
        unknowable -- see index_by_slug; reported as its OWN outcome rather than folded into
        'no-match', which would tell the user the lead is not stale when in fact two of it
        are); 'conflict' (a sustained write race, #16, isolated to that lead); 'skipped'
        (the FRESH status left the triage lifecycle between the read and the write).

        Slugs match by EQUALITY, not `slug_matches`, which is a substring match whose two
        existing callers already disagree about ambiguity. A user typing the narrow form
        is choosing the safer option under decision 3; it must not be the one that
        dismisses leads they did not name.
        """
        from sluice.core.leads import ambiguous_slug_warnings, index_by_slug
        from sluice.core.protocols import VaultConflict
        policy = self.staleness()
        report = self.expire_report(policy)
        store = self.store()
        results = []
        if slugs:
            # index_by_slug, never `{r.slug: r for r in report}`: two stale notes at one slug
            # would otherwise leave whichever came last, so `--expire <slug>` would dismiss
            # one twin while the other was neither dismissed nor reported -- the human sees
            # no sign the second exists. Both are dropped and named instead.
            by_slug, dropped = index_by_slug(report)
            for msg in ambiguous_slug_warnings("expire: stale lead", dropped):
                _log.warning("%s", msg)
            chosen = []
            for s in slugs:
                r = by_slug.get(s)
                if r is not None:
                    chosen.append(r)
                else:
                    results.append((s, "ambiguous" if s in dropped else "no-match"))
        else:
            # The unnarrowed sweep expires the whole stale set, so there is no slug to
            # resolve and nothing to be ambiguous ABOUT: each row is acted on through its own
            # `ref`, which is unique whatever the slugs collide on.
            chosen = list(report)

        today = policy.today
        ttl = self.config.lead_ttl_days
        tag = f"[expire {today}]"
        for r in chosen:
            if r.refused:
                results.append((r.slug, "refused-signoff"))
                continue
            note = (f"{tag} stale: last_seen {r.last_seen} is {r.days}d old "
                    f"(lead_ttl_days={ttl}). Was: {r.status}.")
            try:
                # require_status is what holds never-regress here, and it CANNOT move up
                # into this loop: a check against `r.status` reads the enumeration
                # snapshot, which is stale by construction. Probed against a real vault,
                # that guard is byte-identical to no guard at all.
                wrote = store.update_fields(
                    r.ref, {"status": "dismiss"}, append_note=note, note_tag=tag,
                    require_status=_EXPIRABLE)
            except VaultConflict:
                # Isolated per lead: one conflicting note must not abort the sweep over
                # the rest. Self-heals next run (#16).
                _log.warning("expire: %s lost the write race, left untouched", r.slug)
                results.append((r.slug, "conflict"))
                continue
            except OSError as e:
                # `ref` is a path, and #16's primary threat is a human in Obsidian --
                # a note deleted or renamed mid-sweep is exactly the window this command
                # runs in. Unisolated, the FileNotFoundError escapes to main() and
                # discards the whole outcome list, so the record of what WAS written
                # dies with it. read_leads already skips an unreadable note; merge_cluster
                # and normalize_all_statuses both isolate per item; this is the same rule
                # on the last batch writer that lacked it (#24).
                _log.warning("expire: %s could not be written (%s), left untouched",
                             r.slug, e)
                results.append((r.slug, "unreadable"))
                continue
            results.append((r.slug, "dismissed" if wrote else "skipped"))
        return results

    def dedupe_report(self):
        """The #23 read-path dedup REPORT: suspected-duplicate clusters, each with a
        stable id, computed survivor, conflict flag, and flagged losers. Changes
        nothing."""
        return self._dedupe_report(self.store())

    def dedupe_merge(self, ids):
        """Merge the human-vetted clusters named by `ids`. Recomputes the report
        fresh and matches by id: a stale id (membership changed) -> 'stale'; a
        conflict cluster -> 'conflict' (refused); a sustained write race ->
        'conflict-race'; a survivor whose existing alt_urls is malformed (not a
        JSON list of strings) -> 'malformed' (refused, nothing written, no loser
        touched); every loser archived -> 'merged'; a per-loser archive failure
        (isolated, self-heals next run) -> 'partial'. Returns [(id, outcome)].
        Nothing merges without an id."""
        from sluice.core.protocols import MalformedNoteField, VaultConflict
        store = self.store()
        by_id = {c.id: c for c in self._dedupe_report(store)}
        results = []
        for cid in ids:
            c = by_id.get(cid)
            if c is None:
                results.append((cid, "stale"))
                continue
            if c.conflict:
                results.append((cid, "conflict"))
                continue
            losers = [n for n in c.members if n is not c.survivor]
            try:
                # merge_cluster returns the ARCHIVED loser paths -- a per-loser archive
                # OSError is isolated (that loser stays active, self-heals next run) but
                # must not be reported as a full "merged" (design #3): fewer archived than
                # losers means the archive is genuinely partial.
                # first_seen aggregates only members that actually CARRY the field: a
                # member missing it entirely (a legacy/hand-edited note) must not poison
                # the min() to "" and hide a genuinely earlier date held by another member.
                seens = [n.fm.get("first_seen") for n in c.members if n.fm.get("first_seen")]
                archived = store.merge_cluster(
                    c.survivor.ref, [n.ref for n in losers],
                    alt_urls=[n.fm["url"] for n in losers if n.fm.get("url")],
                    first_seen=min(seens) if seens else "",
                    last_seen=max(n.fm.get("last_seen", "") for n in c.members))
                results.append((cid, "merged" if len(archived) == len(losers) else "partial"))
            except VaultConflict:
                results.append((cid, "conflict-race"))
            except MalformedNoteField:
                results.append((cid, "malformed"))
        return results

    def _layout_store(self):
        """The store, if it implements the vault-only layout pass.

        `reconcile_layout` is deliberately NOT on the Store protocol. Folders are a vault
        MECHANISM -- a store keyed on synthetic ids has none -- and putting it on the contract
        would make every other implementation pretend to honour a concept it does not have. That
        is the leak `ensure_stfolder` was moved out of the protocol to remove, and the surface
        `cmd_init` declines to invent for `Store.display_location()`.

        The contrast with `merge_cluster`, which IS on the protocol, is the useful one: #81
        non-resurrection is a store-agnostic OBLIGATION -- any store can satisfy it, a SQL one with
        a tombstone row -- whereas a folder layout is a mechanism carrying no obligation at all. So
        the coupling here is concrete and CHECKED rather than hypothetical and abstracted: when a
        second store lands and has an opinion about layout, that is the moment to reconsider.

        `getattr` rather than `isinstance(store, Vault)`: importing the concrete Vault into the
        facade to type-test it would put the store implementation back on the composition root's
        import path, which cli.py's lazy-import discipline exists to keep off it."""
        store = self.store()
        fn = getattr(store, "reconcile_layout", None)
        if not callable(fn):
            raise StoreHasNoLayout(
                f"the configured store ({type(store).__name__}) has no folder layout, so "
                f"`leads reconcile` has nothing to reconcile")
        return fn

    def reconcile_report(self) -> dict:
        """The #1 layout REPORT: which lead notes are not in the folder their status implies.
        Changes nothing. See `Vault.reconcile_layout`."""
        return self._layout_store()(apply=False)

    def reconcile(self, apply: bool = False) -> dict:
        """File lead notes into their status-implied folders. `apply=False` (the default) is the
        report -- the same report-first shape as `dedupe_report`/`expire_report`, where a mistyped
        invocation prints a list rather than moving a hundred notes."""
        return self._layout_store()(apply=apply)

    def _naming_store(self):
        """The store, if it implements the filename-reconciliation pass (#151).

        Mirrors `_layout_store` exactly, down to the getattr-not-isinstance reasoning: importing
        the concrete Vault into the facade to type-test it would put the store implementation back
        on the composition root's import path, which cli.py's lazy-import discipline exists to
        keep off it. `reconcile_names` is deliberately NOT on the Store protocol for the same
        reason `reconcile_layout` is not -- a note's on-disk BASENAME disagreeing with its
        frontmatter is a vault mechanism, not an obligation every store shares."""
        store = self.store()
        fn = getattr(store, "reconcile_names", None)
        if not callable(fn):
            raise StoreCannotRename(
                f"the configured store ({type(store).__name__}) cannot rename lead notes, so "
                f"`leads rename` has nothing to do")
        return fn

    def rename_report(self) -> dict:
        """The #151 filename REPORT: which lead notes' basenames disagree with their frontmatter.
        Changes nothing on the vault side. See `Vault.reconcile_names`.

        Additionally computes a BEST-EFFORT dead-letter PREVIEW under `report["deadletter"]`: for
        stores that expose the mechanism, how many OPEN dead-letter rows are filed against a slug
        this report would rename away. This is read-only on the dead-letter side too (no row is
        migrated here -- see `rename(apply=True)` for the write), and it must NEVER fail the whole
        report: a command that only READS is not entitled to fail over a store it isn't writing,
        so any exception (a corrupt/unreadable dead-letter file, a malformed track config, ...)
        becomes `report["deadletter"]["error"]` instead of propagating.

        `refuse_relocated_seen_db=True` on the config load -- even on this read-only path -- is
        deliberate rather than borrowed carelessly from the writers: it is what turns a RELOCATED
        dead-letter store into a loud `deadletter.error` instead of a silent `pending: 0` that
        looks identical to "nothing pending". Both outcomes are caught by the same except below,
        so this choice costs nothing in safety and buys a truthful signal instead of a misleading
        one."""
        rep = self._naming_store()(apply=False)
        rep["deadletter"] = {"pending": 0}
        try:
            from sluice.track.config import load_track_config
            from sluice.track.deadletter import DeadLetterDb, deadletter_path
            tcfg = load_track_config(refuse_relocated_seen_db=True)
            dl = DeadLetterDb(deadletter_path(tcfg.seen_db))
            old_slugs = {slug for slug, _target, _folder in rep["renames"]}
            rep["deadletter"]["pending"] = sum(
                1 for e in dl.open_entries() if e.lead in old_slugs)
        except Exception as e:  # noqa: BLE001 -- best-effort preview; see docstring above.
            rep["deadletter"]["error"] = str(e)
        return rep

    def rename(self, apply: bool = False) -> dict:
        """Rename lead notes whose basename disagrees with their frontmatter (#151). `apply=False`
        (the default) delegates to `rename_report()` rather than re-implementing the read path
        here: `rename_report()` additionally computes the dead-letter PREVIEW
        (`report["deadletter"]`), so delegating is what makes `rename(apply=False)` carry that
        preview too, instead of maintaining a second, narrower read path that would drift from it.

        Under `apply=True` this ALSO migrates the dead-letter store's rows for every note actually
        renamed (Task 9's `DeadLetterDb.rename_lead`): a dead-letter row is keyed on the lead's
        SLUG, and a rename changes that slug, so a proposal filed against the OLD slug would
        otherwise become permanently unreachable by `track confirm`/`track dismiss --lead` the
        moment the note moves out from under it -- the #49 silent-loss class arriving through a
        new door.

        The dead-letter store's reachability is checked *before* any note is renamed, loaded the
        same way `track()`/`track_confirm()` load it (`refuse_relocated_seen_db=True`,
        `DeadLetterDb(deadletter_path(tcfg.seen_db))`) and probed with `check_reachable()` before
        the first write, exactly as `track.engine.confirm` probes it before ITS status write: a
        dead-letter store known to be unreachable refuses the WHOLE operation, and nothing below
        that check runs -- zero notes renamed, not "some notes renamed, their proposals stranded
        with no way to migrate them".

        Once vault renames have actually happened, a PER-PAIR dead-letter migration failure is
        isolated into `report["deadletter"]["failed"]` rather than aborting the loop: the rename
        that already landed on disk is the more important of the two states to preserve. Rolling
        it back because ITS dead-letter migration failed would trade a recoverable problem (a
        stray dead-letter row still filed under the old slug, clearable by hand via `track
        dismiss --lead <old slug>`) for the unrecoverable one this whole feature exists to
        prevent -- a duplicate note minted on the next scrape because the rename never happened."""
        if not apply:
            return self.rename_report()

        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path

        fn = self._naming_store()
        tcfg = load_track_config(refuse_relocated_seen_db=True)
        dl = DeadLetterDb(deadletter_path(tcfg.seen_db))
        # BEFORE any note is renamed -- see the docstring. A raise here propagates straight out
        # of this method: nothing below has executed yet, so a known-unreachable dead-letter
        # store leaves the vault completely untouched.
        dl.check_reachable()

        rep = fn(apply=True)
        refiled = 0
        failed = []
        for old_slug, new_slug, _folder in rep["renames"]:
            try:
                refiled += dl.rename_lead(old_slug, new_slug)
            except Exception as e:  # noqa: BLE001 -- isolate a per-pair failure; see docstring.
                # Vault state (the rename that already happened) is preserved; only the
                # dead-letter migration for THIS pair is reported as failed, so the loop can
                # continue to the next rename rather than abandoning the whole sweep.
                failed.append((old_slug, new_slug, str(e)))
        rep["deadletter"] = {"refiled": refiled, "failed": failed}
        return rep

    def health_report(self, *, include_leads: bool = False) -> list:
        """The per-source health REPORT `job-sluice health` and the MCP `health` tool
        both show -- sorted by source id, mirroring `dedupe_report`/`expire_report`/
        `reconcile_report`'s report-idiom. Changes nothing (a vault WALK, never a write).

        `cmd_list_sources --health` (cli.py) still constructs its own `HealthStore()`
        and walks the registry independently: it also needs enabled/disabled overlay
        state this method does not compute, considered and deliberately not folded in
        here (#105).

        Reads only the source registry and `HealthStore` -- NO vault I/O -- unless
        `include_leads=True`. Default False: `job-sluice health` and the MCP `health`
        tool both call this, both are things a user runs often and cheaply, and an
        unconditional walk would tax every caller for the one feature (#169 §2) that
        needs it. Opting in adds exactly one `read_leads()` pass, which populates each
        `SourceHealth`'s `unjudgeable`/`concluded` facts -- see `SourceHealth` for why
        both terms must come from the SAME lifecycle stage. Classifying whether a rate
        is bad is deliberately not this method's job; `health_report` reports facts."""
        from sluice.core.health import HealthStore
        from sluice.ingest import sources as registry
        health = HealthStore()

        # Both terms come from ONE read_leads() pass, over the leads triage has
        # CONCLUDED about -- `_CONCLUDED`, every triage-owned status except `new`.
        #
        # The denominator used to be DEFAULT_TRIAGE_STATUSES, which over-reported
        # badly and in the direction that trains people to ignore the signal. That set
        # is the SELECTION default, and a lead LEAVES it the moment triage judges it,
        # so in steady state it collapses to roughly the stuck leads themselves: a
        # source with 500 scraped, 480 dismissed, 17 judged and 3 whose JD never
        # arrived printed 3/3 -- 100% -- while being 99.4% healthy. Under `_CONCLUDED`
        # the same source reads 3/500.
        #
        # The cost, stated because the previous comment here stated only the opposite
        # one: including `dismiss` does dilute a source that breaks TODAY against its
        # own history, so a newly-broken mature source shows a percentage rather than
        # 100%. That is the right trade. A false alarm on a healthy source is worse
        # than a muted true one HERE specifically, because this is a supplementary
        # report -- `detect_drift`'s per-run reasons and the ingest breaker are what
        # actually catch a source breaking today, and they are unaffected. `new` is
        # excluded because triage has not reached those leads at all; counting them
        # would understate in the same way, just quietly.
        #
        # Computed once, up front, rather than per-source, so a vault with N sources
        # costs one walk, not N.
        rates = {}
        if include_leads:
            for note in self.store().read_leads(set(_CONCLUDED)):
                src = note.fm.get("source", "")
                bad, total = rates.get(src, (0, 0))
                rates[src] = (bad + (1 if note.status == "unjudgeable" else 0), total + 1)

        def _one(src):
            reason, n = health.explained_streak(src.id)
            bad, total = rates.get(src.id, (0, 0))
            return SourceHealth(id=src.id, kind=src.kind, baseline=health.baseline(src.id),
                                recent=health.counts(src.id),
                                should_retire=health.should_retire(src.id),
                                broken_reason=reason, broken_runs=n,
                                unjudgeable=bad, concluded=total)

        return [_one(src) for src in sorted(registry.all_sources(), key=lambda s: s.id)]

    def triage(self, *, statuses=_status.DEFAULT_TRIAGE_STATUSES, limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        """Run the triage sub-app end to end: classify, dossier-enrich the kept leads,
        judge them, and write the audit trail. `no_llm` skips backend construction
        entirely (`triage()`'s deterministic classify-only path), preserving the
        offline guarantee `--no-llm` has always given `sluice triage run`.

        The primary/fallback field mapping here (`claude_max_*` for primary,
        `cheap_model` for fallback) is triage's own config shape -- other sub-apps
        (cv, apply) have their own `*Config` with their own field names, so this
        mapping is NOT shared and belongs in this method, not in `Sluice.backend`.

        #120: a SECOND backend, built independently of `backend_role`, is threaded
        in as `resolve_backend` when `company_resolve_llm` is on -- tier 3 is bulk
        extraction over the whole needs_review backlog, not judgement, so it stays
        pinned to the cheap "fallback" role even when a user picked `--backend
        primary` for the JUDGE. Its own try/except: `role="fallback"` is STRICT
        (raises rather than degrading on a missing key), and a best-effort
        enhancement must not be able to fail a run whose classify+apply path is
        otherwise fully deterministic.

        Also threads `sources.get` (#109) into `triage.engine.run` as `get_source`,
        the same lazy, inside-the-method import `ingest()` already uses for
        `ingest.base`/`ingest.engine` -- `triage/` itself never imports
        `sluice.ingest` directly."""
        from sluice.core.backends import BackendError
        from sluice.ingest import sources
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        tcfg = load_triage_config()
        # `tcfg.audit_jsonl`, not a second $TRIAGE_AUDIT read: this key was DEAD --
        # declared on TriageConfig and read by nothing, because this line carried its
        # own env read and its own literal default, so setting it in YAML changed
        # nothing and said nothing. The loader resolves it (env -> config key -> the
        # per-system state root), and that one value is what everything uses.
        audit = AuditLog(tcfg.audit_jsonl)
        # Shared by both self.backend() calls below -- the judge's (whatever role the
        # caller picked) and tier 3's resolution backend (always pinned to
        # "fallback", #120) -- so the two calls differ only in the role string, not
        # in a hand-copied kwarg list that could silently drift apart between them.
        _common = dict(
            primary_name=tcfg.primary_backend, primary_model=tcfg.claude_max_model,
            effort=tcfg.claude_max_effort, host=tcfg.claude_max_host,
            claude_path=tcfg.claude_max_path, fallback_name=tcfg.fallback_backend,
            fallback_model=tcfg.cheap_model)
        backend = None if no_llm else self.backend(backend_role, **_common)
        resolve_backend = None
        if not no_llm and tcfg.company_resolve_llm:
            try:
                resolve_backend = self.backend("fallback", **_common)
            except BackendError as e:
                _log.warning(
                    "company resolution's tier-3 backend unavailable, tier 3 disabled "
                    "this run: %s", e)
        cache = self.dossier_cache(self._dossier_dir(), tcfg.ttl_days,
                                   self.config.min_jd_chars)
        store = self.store()
        return _triage_run(store, tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm, get_source=sources.get,
                           resolve_backend=resolve_backend,
                           reverdict_scope=self._reverdict_scope(store))

    def _reverdict_scope(self, store) -> str:
        """A stable, NON-EMPTY identity for the lead store #223's notice is about.

        The notice is a claim about one store's accumulated notes, so acknowledging it
        must not silence it for a different one. Resolved here, at the application
        boundary, because `Store` does not declare `dir` and the engine sits on the far
        side of that seam.

        Non-empty is the load-bearing part. An earlier version passed `getattr(store,
        "dir", "")` straight through, so ANY store without a `dir` -- every future
        non-directory implementation -- resolved to `""`, and all of them shared one
        acknowledgement: the first to acknowledge silenced the notice for the rest,
        including the `dismiss` writes it exists to hold back. That is the per-vault harm
        this key exists to prevent, reintroduced through the fix for the seam violation.

        So the fallback is built from what the application knows rather than from the
        store object: the configured store NAME plus the same `VAULT_DIR`-then-config
        precedence `stores/vault.py`'s factory itself uses. Two dir-less stores of the
        same name under the same config ARE the same store and correctly share a key;
        two of different names do not.
        """
        named = getattr(store, "dir", "")
        if named:
            return f"vault:{named}"
        kind = getattr(self.config, "store", "vault")
        configured = os.environ.get("VAULT_DIR") or getattr(self.config, "vault_dir", "")
        return f"{kind}:{configured}"

    def compose_cv(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False,
                    no_serve=False, backend_role="auto", include_stale=False):
        """Run the cv sub-app: compose (and, unless dry_run, render) a CV for one
        shortlisted lead or for every shortlisted lead. Returns the list of CvResult.

        A `lead` fragment matching NO shortlist note returns `[]`; one matching TWO OR MORE
        returns a `skipped-ambiguous` CvResult per candidate and composes for none of them
        (see the guard below). Both are refusals the CLI must exit non-zero on.

        The renderer is resolved for a dry run TOO, and that is a correction rather than
        an oversight repaired: `renderer=None` on a dry run also switched off the seam's
        optional `precheck` grammar hook, which `cv/engine.py` reaches via
        `getattr(renderer, "precheck", None)`. Measured 2026-08-06 -- one CV, gate-clean
        and unparseable by the `template` renderer, reported `status=dry-run,
        violations=[]` on a dry run and `status=skipped-gate` with a `FORMAT:` violation
        on the real run. A dry run IS the cheap preview, and it was false-greening exactly
        the CV a real run refuses.

        Construction is still allowed to FAIL without killing the dry run, which is what
        the original `None` was reaching for: a missing template file or an uninstalled
        WeasyPrint is a config problem with nothing to do with this CV, so a preview must
        not die on it. What a dry run skips is the RENDER and the WRITES -- not the cost.
        `cv/engine.py`'s `run_one` calls `_compose.compose(backend, ...)` and then
        `run_audit(backend, ...)` ABOVE its `if dry_run:` return, so a dry run still
        spends a composition and an audit call per lead. Stated because the earlier
        wording here said a preview "costs nothing", which is the reading that makes a
        `--dry-run` over a large shortlist look free. So a `RenderError` is caught,
        warned about
        NAMING the lost check (a silently weaker dry run is the thing being fixed), and
        the run proceeds unchecked. An unknown `cv.renderer` NAME is deliberately not
        caught: that is `plugins.get`'s "fail loudly at construction, listing the valid
        names", and a dry run that hid it would report success for a pipeline that cannot
        run at all.

        cv's config maps to Sluice.backend's fields via compose_model/compose_effort/
        compose_host/compose_claude_path -- NOT triage's claude_max_* fields. That
        mapping belongs here, not in Sluice.backend, same reasoning as `triage()`.

        Raises `ValueError` naming the valid choices on an unrecognised `backend_role`
        -- `Sluice.backend`'s own `BackendError`, re-raised here (#131: mcpserver.py's
        cv_run passes `backend` straight through with no duplicate copy of the choice
        set, and its own isolation sweep forbids it importing `BackendError` directly,
        so the translation belongs at this layer, the same way `dismiss_lead` raises
        `ValueError` for ITS OWN malformed `reason` directly rather than leaving it to
        a caller). cli.py's `--backend` argparse `choices` already reject a bad value
        before ever reaching here, so this is unreachable from the CLI in practice."""
        from sluice.core.backends import BackendError
        from sluice.cv.config import load_cv_config
        from sluice.cv.engine import (CvResult, missing_prerequisites, run_batch,
                                      run_one)
        from sluice.core.leads import slug_matches
        from sluice.core.protocols import VaultConflict

        cvcfg = load_cv_config()
        if no_serve:
            cvcfg.served_dir = ""  # engine still renders; serve is skipped when dir is empty

        # #242: the two config-level preconditions, checked ONCE and FIRST. They are
        # properties of the install rather than of a lead, so this is not in run_one: a
        # per-lead check emits N identical lines for one fixable thing, and `--lead` used to
        # surface a missing baseline as a TRACEBACK out of `_read`'s bare open.
        #
        # BEFORE the renderer and the backend, not merely before the dossier fetch. Measured:
        # on a bare install the renderer raises first (`No module named 'weasyprint'`), so a
        # check placed after it never runs for exactly the newcomer this exists to help, and
        # they get a traceback about a rendering library instead of "you have no CV yet".
        #
        # ValueError, so `main`'s handler turns it into the clean exit-2 usage error
        # docs/USAGE.md promises for a config problem. A dry run is refused too: previewing a
        # run that cannot possibly compose is the false green this check exists to remove.
        store = self.store()
        prereqs = missing_prerequisites(store)
        if prereqs:
            raise ValueError(
                "cv: this vault is not set up to compose yet:\n  - "
                + "\n  - ".join(prereqs))
        if dry_run:
            # See the docstring: a dry run wants the renderer for its `precheck` alone,
            # and must survive a renderer it cannot build. The engine never calls
            # `render()` on this path -- run_one returns `dry-run` above the render line.
            from sluice.core.protocols import RenderError
            try:
                renderer = self.renderer(cvcfg)
            except RenderError as e:
                renderer = None
                _log.warning(
                    "cv --dry-run: renderer %r could not be constructed (%s), so its "
                    "format precheck did NOT run -- a real run may still report "
                    "skipped-gate for this lead", getattr(cvcfg, "renderer", ""), e)
        else:
            renderer = self.renderer(cvcfg)
        try:
            backend = self.backend(
                backend_role, primary_name=cvcfg.primary_backend,
                primary_model=cvcfg.compose_model, effort=cvcfg.compose_effort,
                host=cvcfg.compose_host, claude_path=cvcfg.compose_claude_path,
                fallback_name=cvcfg.fallback_backend, fallback_model=cvcfg.cheap_model,
                timeout=cvcfg.compose_timeout)
        except BackendError as e:
            raise ValueError(str(e)) from e
        cache = self.dossier_cache(self._dossier_dir(), cvcfg.ttl_days,
                                   self.config.min_jd_chars)
        # Built ONCE here and passed to both branches, so the single-lead and batch paths
        # cannot disagree about what stale means or about --include-stale (#9).
        policy = self.staleness(include_stale=include_stale)

        if all_shortlist:
            return run_batch(store, cvcfg, backend, cache, renderer=renderer,
                             limit=limit, dry_run=dry_run, policy=policy)
        notes = [n for n in store.read_leads({"shortlist"}) if slug_matches(n, lead)]
        if not notes:
            return []
        if len(notes) > 1:
            # Refuse rather than compose against whichever twin the store listed first.
            # `slug_matches` is a SUBSTRING match, so one typed fragment can name two
            # genuinely different leads -- and once the scan is recursive two notes can
            # claim one slug outright (#1), which widens it. `notes[0]` below would then
            # tailor a CV to a job the user did not name and seat the send-ready
            # `tailored_cv` pointer on that note, which `apply prep` reads: a wrong-identity
            # write in the never-clobber family, and one that costs an LLM call and a render
            # to make. Every sibling single-lead path already refuses instead of guessing --
            # `apply/select.py:select_one`, `track confirm`, `Sluice.expire` -- so this is
            # that policy reaching the last two paths that still picked one, not a new one.
            #
            # One result PER candidate, in run_batch's own `skipped-ambiguous` vocabulary,
            # because the CLI must NAME the twins for the user to retype a longer fragment
            # against; a single row could name only one of them. Nothing is written, and the
            # refusal is upstream of every backend call. `slug_matches` itself is left alone:
            # `expire` narrows by EQUALITY for its own stated reason (see its docstring), and
            # tightening the shared matcher here would silently change `apply` too.
            return [CvResult(n.ref, "skipped-ambiguous") for n in notes]
        # The direct single-lead path overwrites an existing tailored_cv (guard_existing_cv
        # defaults False, unlike run_batch) -- a user re-tailoring one lead by name means it.
        # A lead HELD for sign-off (pending_cv set) is the exception: run_one skips it before
        # compose (#60 latch), so re-running `cv --lead` will NOT re-tailor it -- the
        # sanctioned way to force a fresh compose of a held lead is `cv signoff --discard`.
        # Under sustained write-race exhaustion set_tailored_cv still raises VaultConflict
        # (#16); that must not escape to the CLI as an unhandled traceback.
        try:
            return [run_one(notes[0], store, cvcfg, backend, cache, renderer=renderer,
                            dry_run=dry_run, policy=policy)]
        except VaultConflict as e:
            _log.warning("cv re-tailor for %s lost the write race: %s", notes[0].ref, e)
            # run_one stamps dossier_failed onto the exception before re-raising it (see
            # its own comment) precisely so THIS catch does not under-report "N CV(s)
            # composed blind" for a lead whose dossier was blocked by the SSRF guard and
            # which then also lost the write race -- the same defect run_batch's
            # catch-all was fixed against one commit ago; this is its second call site.
            # `getattr(..., False)` also covers a VaultConflict raised by code that
            # predates #18 and so never carries the attribute.
            return [CvResult(notes[0].ref, "error",
                             dossier_failed=getattr(e, "dossier_failed", False))]

    def sign_off_cv(self, *, lead, accept=True, confirm=None, require_pending=None):
        """Resolve a shortlisted lead by slug ONCE and resolve its #60 sign-off hold via
        the store: accept -> promote pending_cv to the send-ready `tailored_cv` pointer;
        `accept=False` (discard) -> clear the markers, freeing a fresh compose.

        `confirm`, when given, is called with (slug, pending_cv, claims) AFTER the lead
        is resolved and BEFORE the store write -- so a caller can show the flagged
        claims and decide while this method itself does no I/O; a falsey return
        aborts. Resolving once and handing `note.ref` straight to the store means a
        separate peek and execute can never diverge onto different substring matches.

        `require_pending` (#131 decision 13), when explicitly given, is passed straight
        through to `store.sign_off` (mirroring `require_status`/`require_blank`'s
        existing shape: caller-supplied value, compared against the FRESH read at
        write time). When `confirm` is given and `require_pending` was NOT explicitly
        overridden, this method derives it automatically from the SAME `pending_cv`
        value the confirm callback just saw -- the snapshot at resolution time -- so
        `Vault.sign_off`'s CAS transform can catch a race between resolution (plus any
        I/O `confirm` performs -- an interactive human prompt, or an MCP client's own
        round trip) and the write. This is the first sign-off-hold refusal in this
        codebase to actually be CAS-fresh in every confirm-mediated path, not merely
        the discard path.

        Returns a SignOffResult. outcome is one of: not_found (no lead matched),
        ambiguous (candidates carries the matching slugs), nothing (no pending_cv to
        resolve), aborted (confirm declined), promoted | discarded | collision | stale
        (the store's own verdict, threaded through verbatim), or conflict (a sustained
        write race, #16, never an unhandled traceback)."""

        from sluice.core.leads import slug_matches
        from sluice.core.protocols import VaultConflict
        store = self.store()
        # Resolved over EVERY triage-owned status, not `shortlist` alone and not
        # `_EXPIRABLE`. A held lead can legitimately leave shortlist -- `sluice triage
        # run --status shortlist` re-judges it and may write `research`/`needs_review`/
        # `dismiss` -- and a narrower lookup then reports "no match" for a hold that
        # demonstrably exists. `dismiss` is IN this set precisely because it is the one
        # triage verdict `_EXPIRABLE` omits (being expire's own destination) (#9).
        notes = [n for n in store.read_leads(frozenset(_status.TRIAGE_OWNED))
                 if slug_matches(n, lead)]
        if not notes:
            return SignOffResult(outcome="not_found")
        if len(notes) > 1:
            # decision 15: candidates is always a sorted slug list, matching get_lead's
            # shape everywhere -- never the old " | "-joined ref string, which an MCP
            # client would have to parse back into data, incorrectly, if a ref itself
            # ever contained that substring.
            return SignOffResult(outcome="ambiguous",
                                 candidates=sorted(n.slug for n in notes))
        note = notes[0]
        pending = note.fm.get("pending_cv") or ""
        if not pending:
            return SignOffResult(slug=note.slug, outcome="nothing")
        if confirm is not None:
            raw = note.fm.get("needs_signoff")
            claims = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    claims = parsed if isinstance(parsed, list) else [str(parsed)]
                except (ValueError, TypeError):
                    claims = [str(raw)]
            if not confirm(note.slug, pending, claims):
                return SignOffResult(slug=note.slug, outcome="aborted")
        effective_require_pending = require_pending
        if effective_require_pending is None and confirm is not None:
            effective_require_pending = pending
        # require_pending is passed ONLY when set, mirroring update_fields's own
        # require_status/require_blank call convention elsewhere in this class (e.g.
        # the leads-expire call site) -- never as an explicit `require_pending=None`.
        # A real Store's own default is already None, so this changes nothing for it;
        # it is what keeps a pre-#131 test double or Store implementation that has not
        # yet grown a `require_pending` parameter (this method's ordinary
        # confirm=None callers, like the CLI's --discard/--yes flows, never need it)
        # working unmodified.
        kwargs = {"accept": accept}
        if effective_require_pending is not None:
            kwargs["require_pending"] = effective_require_pending
        try:
            outcome = store.sign_off(note.ref, **kwargs)
            return SignOffResult(slug=note.slug, outcome=outcome)
        except VaultConflict as e:
            _log.warning("cv signoff for %s lost the write race: %s", note.ref, e)
            return SignOffResult(slug=note.slug, outcome="conflict")

    def dismiss_lead(self, *, lead: str, reason: str,
                     note_tag: str | None = None) -> DismissResult:
        """Resolve `lead` by EXACT slug equality (never substring -- #131 decision 4:
        no CLI precedent to inherit a looser matcher from, and the caller may be an
        LLM whose `lead` string derives from attacker-influenced scraped text) over
        every TRIAGE_OWNED status, and dismiss it: status -> "dismiss", with `reason`
        appended to relevance_notes under an idempotency tag so a same-day re-dismiss
        is a real `unchanged`, not a duplicate note.

        Refuses (writes nothing) rather than picks when the exact slug names TWO OR
        MORE notes (a slug collision from the recursive scan, #1) -- via the shared
        `index_by_slug` verdict every other multi-writer consumer already uses.

        Guards, both CAS-fresh (re-read inside the write transform, never from the
        snapshot this method itself read to resolve the lead):
          - require_status=_DISMISSABLE_FROM (the FULL TRIAGE_OWNED set, "dismiss"
            included -- see _DISMISSABLE_FROM's own comment for why NOT _EXPIRABLE).
          - require_blank={"pending_cv"} -- refuses a lead holding an unsigned
            composed CV; the refusal names the remedy (cv_signoff(lead=..., discard=
            true), on this same tool surface elsewhere).

        `note_tag` defaults to f"[dismiss {date.today().isoformat()}]", matching the
        established [triage <date>]/[expire <date>] convention -- overridable only
        for tests exercising idempotency deterministically (never exposed to an MCP
        client, #131 decision 5).

        Raises ValueError naming the field if `reason` is blank or not frontmatter-
        safe -- dropping a dismissal's reasoning erases the entire point of the call,
        so this refuses BEFORE any store read, matching create_lead's identical
        raise-on-payload-fields discipline (decision 9)."""
        from sluice.core.leads import index_by_slug
        from sluice.core.protocols import VaultConflict
        from sluice.core.vault import frontmatter_safe
        if not reason or not reason.strip():
            raise ValueError("reason must not be blank")
        safe_reason = frontmatter_safe(reason)
        if safe_reason is None:
            raise ValueError(
                f"reason {reason!r} is not safe to write into frontmatter (must be "
                f"printable and contain no \" or \\)")
        store = self.store()
        notes = store.read_leads(frozenset(_status.TRIAGE_OWNED))
        index, dropped = index_by_slug(notes)
        if lead in dropped:
            return DismissResult(outcome="ambiguous",
                                 candidates=sorted(n.slug for n in dropped[lead]))
        note = index.get(lead)
        if note is None:
            return DismissResult(outcome="not_found")
        if note.status == "dismiss":
            # Nothing for THIS call to transition -- the same "nothing to act on"
            # shape sign_off_cv's own `nothing` outcome already uses one method
            # above (checked against the snapshot, no store write attempted).
            # `dismiss -> dismiss` is a legitimate no-op transition, never a
            # regression (see _DISMISSABLE_FROM's own comment for why "dismiss" is
            # included in the required-status set at all): a lead a human or an
            # earlier call already filed away has nothing left to dismiss, and
            # re-running the write merely to append another same-purpose note to an
            # already-closed lead's history serves no purpose the tag-idempotency
            # mechanism below exists to serve for an ACTIVE lead.
            return DismissResult(slug=note.slug, status="dismiss", outcome="unchanged")
        tag = note_tag or f"[dismiss {date.today().isoformat()}]"
        snapshot_notes = note.fm.get("relevance_notes", "") or ""
        tag_absent_at_snapshot = tag not in snapshot_notes
        try:
            # The appended text carries the tag ITSELF, matching expire()'s
            # `note = f"{tag} stale: ..."` convention -- update_fields's own
            # idempotency check (`if note_tag not in current`) tests for the tag as a
            # SUBSTRING of relevance_notes, so the tag must actually be written into
            # the note for a later call (same-day repeat, or the losing side of a
            # real race) to ever find it there. Passing `note_tag=tag` alongside a
            # body that does NOT itself contain `tag` would make the presence check
            # permanently un-satisfiable, and every repeat call would re-append.
            wrote = store.update_fields(
                note.ref, {"status": "dismiss"}, append_note=f"{tag} {safe_reason}",
                note_tag=tag, require_status=_DISMISSABLE_FROM,
                require_blank=frozenset({"pending_cv"}))
        except VaultConflict as e:
            _log.warning("dismiss_lead: %s lost the write race: %s", note.ref, e)
            return DismissResult(slug=note.slug, outcome="conflict")
        note_appended = tag_absent_at_snapshot and wrote
        if wrote:
            return DismissResult(slug=note.slug, status="dismiss", outcome="dismissed",
                                 note_appended=note_appended)
        # UNFILTERED: this read only DIAGNOSES a refusal, and a status filter would
        # drop a note whose status drifted to a non-canonical value (`normalize`
        # passes an unknown value through unchanged), making a genuine refused_status
        # report as the benign `unchanged` -- the same mislabelling the ref-then-slug
        # fallback below exists to prevent, arriving by a different route.
        fresh_notes = store.read_leads()
        fresh = next((n for n in fresh_notes if n.ref == note.ref), None)
        if fresh is None:
            # `ref` is a path, and it can have moved (e.g. a concurrent `leads
            # reconcile --apply` relocated the note to its status-implied
            # folder) in the window between this write attempt and this
            # re-read -- without a fallback, that makes `fresh` None and the
            # code below fall back to the STALE pre-write snapshot's status,
            # so a genuine refused_status could incorrectly report as the
            # more benign `unchanged` (Minor #11, final whole-branch review).
            # ONE fallback re-resolution by slug (the basename, unaffected by
            # a folder move) before giving up and using the stale snapshot --
            # reusing the SAME `fresh_notes` read rather than a second store
            # scan, so the two lookups can never disagree about WHEN they saw
            # the vault.
            fresh = next((n for n in fresh_notes if n.slug == note.slug), None)
        fresh_status = fresh.status if fresh is not None else note.status
        if fresh_status not in _DISMISSABLE_FROM:
            return DismissResult(slug=note.slug, status=fresh_status,
                                 outcome="refused_status", note_appended=False)
        if fresh is not None and (fresh.fm.get("pending_cv") or ""):
            return DismissResult(slug=note.slug, status=fresh_status,
                                 outcome="refused_signoff_hold", note_appended=False)
        return DismissResult(slug=note.slug, status=fresh_status, outcome="unchanged",
                             note_appended=False)

    def add_evidence(self, *, kind: str, name: str, fields: dict, body: str = "") -> str:
        """Propose one evidence entry (#164). Returns the store's opaque handle for it --
        non-empty on success, and safe to show a user; NOT promised to be a path (the
        vault's happens to be one), on the same terms as `Store.write_document`'s.

        Named add_evidence rather than propose_evidence deliberately: the isolation
        sweep in tests/test_mcpserver.py matches a CALL by attribute name, so a facade
        method sharing a Store write method's name would be swept as a direct store
        write the moment mcpserver.py called it -- the same reason create_lead differs
        from upsert and sign_off_cv differs from sign_off. That stopped being
        anticipatory at #175: `mcpserver.propose_evidence` calls this, so renaming this
        method to match the Store member it wraps -- the obvious tidy-up, since every
        other name here mirrors its store's -- turns that sweep red immediately.
        Measured, not assumed: renaming the method and its call sites makes the sweep
        report `call to .propose_evidence(...)`. The divergence is the point, not an
        oversight.

        Never citable on its own, and the mechanism is the STORE's obligation, not this
        signature's: `fields` is a caller-supplied mapping and could carry `verified`
        (this docstring used to claim otherwise), so what holds the property is
        `Store.propose_evidence`'s requirement to reject an undeclared field key by name
        -- `_render_evidence_note` in the one store that exists -- plus its requirement to
        write where `read_evidence` cannot see it."""
        return self.store().propose_evidence(kind, name=name, fields=fields, body=body)

    def list_evidence(self, *, kind: str, pending: bool = False) -> list:
        """Citable entries for one EVIDENCE_KINDS kind (default), or -- pending=True --
        the not-yet-verified queue verify_evidence_interactive offers for review.
        Named distinctly from Store.read_evidence/read_pending_evidence for the same
        isolation-sweep reason add_evidence is."""
        store = self.store()
        return (store.read_pending_evidence(kind) if pending
                else store.read_evidence(kind, verified_only=True))

    def verify_evidence_interactive(self, *, kind: str, asker, only: str | None = None,
                                    today: str | None = None) -> dict:
        """Offer each pending entry for review and promote the ones a human accepts.

        Interactive by construction. There is no --all and no --yes anywhere in this
        feature: this is the ONE operation that grants citability to the CV
        fabrication gate, and a bulk flag is the `--verified` hole one level up that
        EVIDENCE_KIND's own docstring already refuses to expose. `only` FILTERS which
        pending entries are offered for review -- it narrows the queue, it never
        auto-approves what it narrows to.

        A non-matching `only` is reported, not silently absorbed: `report["not_found"]`
        holds `[only]` whenever `only` was given and matched no pending entry, so the
        caller can tell "you named an id that isn't pending" apart from "nothing is
        pending at all" -- the two would otherwise be the identical all-empty report,
        which is exactly the quiet-wrong-default class this codebase engineers out
        everywhere else (empty-config-abstains, a retired config key raising by name
        rather than falling through). It is populated regardless of `asker.interactive`:
        the filter itself decided nothing matched before interactivity is even
        consulted, so a non-interactive asker still gets the same signal.

        `only` matches on EITHER the title verbatim or the title's reduced form, and
        needs both. Verbatim first, because `--id` has to match what `... list
        --pending` DISPLAYS, which is `entry["title"]` -- the entry's real basename, and
        for a hand-added `_inbox/My Entry.md` that is `My Entry`, which no reduction
        produces (#164 whole-branch review, IMPORTANT 2). Reduced as well, because for
        an entry created through `... add` the title is the slug `propose_evidence`
        filed it under, while `only` is documented (and typed by a user) as the same
        NAME `--name` took -- so `--id "Beta Thing"` has to find `beta-thing` (#164
        Task 7 review, IMPORTANT 3). `evidence_slug` is idempotent, so a user typing
        an already-reduced slug is served by both arms alike. A value that does not
        reduce at all (all punctuation, say) keeps only the verbatim arm, which simply
        matches nothing -- rather than letting evidence_slug's ValueError escape this
        filter.

        Under a non-interactive asker nothing is promoted -- `report["interactive"]`
        is False and every pending entry (post-`only`-filter) is reported `skipped`,
        which is what a caller prints. Gated on the asker's CLASS ATTRIBUTE
        (`asker.interactive`) rather than sys.stdin.isatty(), for the reason
        onboard/ask.py::TtyAsker records: deriving it independently made the interactive
        half unreachable under pytest, where isatty() is always False.

        `reviewed` is the EXACT text `Store.read_pending_evidence_text` just returned
        and this loop just showed the human -- never re-derived -- so
        Store.verify_evidence's compare-and-set compares against what they actually
        read, not a reconstruction of it. It goes through that contract member rather
        than `open(entry["path"])`, which is what this loop used to do: `path` was a
        required contract key purely to serve this one `open`, and a store-agnostic
        facade reaching through the seam at a filesystem is the inversion `read_criteria`
        was introduced to remove (#164 review, H3). The read stays FRESH either way --
        the contract requires that of the member, for the compare-and-set's sake.

        `report["failed"]` holds `(title, reason)` pairs for entries that could not be
        read or promoted. One failing entry never aborts the batch: see the loop's own
        comment for the measured starvation that isolation closes.

        The `verify this entry? [y/N]` prompt below is the one user-facing string this
        module shows anybody, and `tests/onboard_prose.py`'s roster does not reach it --
        that sweep walks `sluice.onboard` and `sluice.evidence`, never `sluice.core`. It is
        covered instead by `test_no_command_message_names_a_taxonomy_word`
        (tests/test_evidence_cli.py), which drives a real interactive `verify` and sweeps
        what the asker was SHOWN, the same where-it-runs answer `evidence/commands.py`'s
        own messages get. Witnessed: a taxonomy word planted in that f-string is caught by
        that test and by nothing else in the suite.
        """
        from sluice.core.vault import evidence_slug

        store = self.store()
        # `self._today` is a zero-arg CALLABLE (see staleness() above), not a string:
        # `today or clock()` must call it, mirroring the one other place this class
        # resolves its injected clock.
        clock = self._today or _today
        today = today or clock()
        report = {"promoted": [], "skipped": [], "unchanged": [], "not_found": [],
                 "failed": [],
                 "interactive": bool(getattr(asker, "interactive", False))}
        pending = store.read_pending_evidence(kind)
        if only:
            try:
                reduced = evidence_slug(only)
            except ValueError:
                reduced = None  # cannot reduce at all -- the verbatim arm alone applies
            pending = [e for e in pending
                       if e["title"] == only or e["title"] == reduced]
            if not pending:
                report["not_found"] = [only]
        if not report["interactive"]:
            report["skipped"] = [e["title"] for e in pending]
            return report
        for entry in pending:
            # PER-ITEM isolation, the shape sluice/evidence/wizard.py's capture loop
            # already uses: one entry that cannot be read or promoted must not abort the
            # batch. Measured before this (#164 review, H2) with pending
            # ['alpha', 'mike', 'november'] and a name already taken in the citable set:
            # the FileExistsError unwound past this loop and discarded `report` whole, so
            # the user saw one bare `[Errno 17] File exists: <path>` and exit 1, whatever
            # had already been promoted this run went unreported, `november` was never
            # offered at all -- and EVERY later run aborted at the same entry, so the one
            # path to citability starved permanently and `cv run` kept reporting
            # `skipped-gate`, the fabrication verdict this feature exists to prevent.
            try:
                reviewed = store.read_pending_evidence_text(kind, entry["title"])
            except (OSError, ValueError) as e:
                report["failed"].append((entry["title"], _evidence_failure_reason(e)))
                continue
            if not asker.confirm(f"{reviewed}\nverify this entry? [y/N] "):
                report["skipped"].append(entry["title"])
                continue
            try:
                promoted = store.verify_evidence(kind, entry["title"], today=today,
                                                 reviewed=reviewed)
            except (OSError, ValueError) as e:
                report["failed"].append((entry["title"], _evidence_failure_reason(e)))
                continue
            if promoted:
                report["promoted"].append(entry["title"])
            else:
                report["unchanged"].append(entry["title"])
        return report

    def create_lead(self, *, title: str, company: str, url: str, location: str = "",
                    salary: str = "", job_type: str = "", source: str = "manual"
                    ) -> CreateLeadResult:
        """Create a lead note directly -- for a job a human found that no scanner
        ingested (#131 decision 9-12). Raises ValueError naming every unsafe/invalid
        field (matching list_leads's "name the full bad set, never silently return
        empty" convention): dropping company/title changes which note gets created or
        whether upsert's blank-identity gate refuses outright, so this raises rather
        than abstains -- create_lead does its OWN validation up front and never
        relies on _render_new's abstain-and-blank fallback (decision 7's separate,
        narrower defense-in-depth for the pre-existing scraper path).

        `url` is REQUIRED (no default, at both this facade and the tool signature)
        and must be http(s) -- matching apply/select.eligibility's own rule -- so a
        hand-created lead is apply-eligible by construction. `location` stays
        optional at both layers: an unknown/blank location is real, valid data, not
        an error condition.

        Reports upsert's own six-member outcome vocabulary VERBATIM, never a bare
        "created". Identity is company+title (url is not part of vault identity):
        a SECOND call at that same identity bumps last_seen ONLY, reported as
        "updated" when the incoming url (or, absent a url match, the location)
        proves the same posting, or "merged" when neither does (inconclusive
        evidence) -- UNLESS the two locations are proven DIFFERENT (two non-blank,
        non-overlapping locations), in which case this call creates a genuinely
        NEW note instead ("created" again -- a second real note at the same
        company+title; see "advance" in Vault._reconcile). Both "updated" and
        "merged" are a bare last_seen bump, with the incoming url/salary/location
        NOT recorded.

        Slug resolution (#131 post-final-review fix) is `store.upsert`'s OWN
        report of which note it just touched (`result.slug`), never a guess
        reconstructed after the fact. Earlier rounds tried re-reading
        `store.read_leads()` and filtering by company+title -- a proven-wrong
        approach: company+title alone can match MORE than one note (a
        proven-different location seats a second note at that identity), and
        no post-hoc filter over the finished set can always tell which of them
        THIS call's write actually resolved to, because the store's own
        resolution walks candidate names in order and stops at the first
        non-advance verdict. `result.slug` is that answer, correct by
        construction -- see `UpsertResult`'s own docstring (core/protocols.py)
        for the full argument and the concrete reproduction that motivated it.
        `slug` is "" only for "refused"/"merged_away"/"merged_away_unproven",
        which write nothing and so never have a slug to report in the first
        place -- never for "created"/"updated"/"merged", which are now always
        correctly resolved.

        `search` is never persisted anywhere (verified by grep across sluice/: no
        reader of Lead.search exists outside _row_to_lead's own construction) -- this
        method passes search="" rather than expose a parameter for a field that goes
        nowhere. Calls store.upsert() directly, never VaultSink, so seen.db is
        untouched (decision 11) -- a later genuine scrape of the same posting is not
        silently skipped by this manual entry."""
        from sluice.core.leads import Lead, is_http_url
        from sluice.core.roletype import DECLARED, normalise_role_type
        from sluice.core.vault import frontmatter_safe
        fields = {"title": title, "company": company, "location": location,
                 "salary": salary, "job_type": job_type, "source": source}
        # `value.strip()`, not bare `value`: frontmatter_safe's OWN falsy-or-
        # all-whitespace rule already treats " " the same as "" ("nothing worth
        # writing", its own docstring), so a bare-truthy guard here would flag a
        # whitespace-only title/company as UNSAFE and raise -- when the intent is
        # to let it fall through to upsert's own blank-identity gate instead
        # (test_refused_returns_no_slug pins the "refused", not raised, outcome).
        bad = sorted(name for name, value in fields.items()
                    if value.strip() and frontmatter_safe(value) is None)
        if not url or not is_http_url(url) or frontmatter_safe(url) is None:
            bad = sorted(set(bad) | {"url"})
        if bad:
            raise ValueError(
                f"unsafe or invalid field(s): {bad} (must be printable, contain no "
                f"\" or \\, and url must be present and http(s))")
        store = self.store()
        # #223 §2.1's third origin. This is the case where `job_type` is UNAMBIGUOUSLY
        # the user's -- they typed it into `leads add` or the MCP write tool -- so it
        # is `declared`, the provenance the relevance gate is allowed to act on. The
        # stamp is repeated here rather than centralised in `_row_to_lead` because this
        # path never touches it: `create_lead` builds the `Lead` by hand and calls
        # `store.upsert` directly (decision 11, so seen.db is untouched).
        #
        # `normalise_role_type` warns and blanks an unrecognised value rather than
        # raising, unlike the `bad` check above. Deliberate: that check guards fields
        # whose loss changes WHICH note is created or whether the blank-identity gate
        # refuses, and none of them has a closed set to fall back to. A job_type that is
        # neither contract nor permanent has one -- "unstated" -- and the lead is still
        # worth creating without it.
        job_type = normalise_role_type(job_type)
        result = store.upsert(Lead(source=source, search="", title=title,
                                   company=company, url=url, location=location,
                                   salary=salary, job_type=job_type,
                                   job_type_source=DECLARED if job_type else ""))
        if result.outcome not in ("created", "updated", "merged"):
            return CreateLeadResult(outcome=result.outcome)
        return CreateLeadResult(outcome=result.outcome, slug=result.slug)

    def prep(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False,
             include_stale=False):
        """Run the apply sub-app's prep step: stage the tailored CV and build an
        application packet for one shortlisted lead, or preview the whole ready
        queue. Always returns a list of `apply.engine.PrepResult` so a single lead
        and an all_shortlist batch share one return shape for the caller.

        Apply is OFFLINE by contract -- no backend, no dossier, just the store --
        so unlike `triage`/`compose_cv` this method never touches `Sluice.backend`
        or `Sluice.dossier_cache`.

        `dry_run` (single lead only; `preview_all` already has its own no-write
        preview mode for all_shortlist) mirrors `engine.prep_one`'s select-then-
        build-packet shape but skips `cvfile.stage` entirely, so a dry run changes
        nothing on disk. The result is wrapped as a single-element list with
        status "previewed" (an eligible lead whose packet was built) or "skipped"
        (select_one found no eligible match) -- the same vocabulary
        `PrepResult.status` uses everywhere else, even though `prep_one`'s
        non-dry-run success status is "staged". The CLI is what re-labels the
        dry-run "previewed" case back to today's "dry-run" wording; see
        cmd_apply_prep."""
        from sluice.apply import engine, packet, select
        from sluice.apply.config import load_apply_config

        cfg = load_apply_config()
        store = self.store()
        # THREE branches reach selection, not two: the dry-run single-lead path calls
        # select_one DIRECTLY, bypassing prep_one. Miss it and `prep --lead X --dry-run`
        # previews a lead the real run refuses, so the preview lies about what will
        # happen -- and --include-stale is dead on that path (#9).
        policy = self.staleness(include_stale=include_stale)
        # ONE read per prep() call, not one per lead: all_shortlist loops the whole
        # shortlist inside engine.preview_all, and a per-lead re-fetch would re-read
        # the same candidate note N times and let two leads in one batch disagree if
        # the note changed mid-run. Reading it here, once, and passing it into every
        # branch below is what keeps that impossible by construction rather than by
        # convention.
        profile = store.read_candidate_profile()
        # The clock is ALREADY resolved inside staleness() above (self._today or
        # _today, called exactly once). Reading the date back off the frozen policy
        # -- rather than resolving self._today a second time here -- is what keeps
        # this whole prep() call pinned to one date; two independent resolutions
        # could straddle midnight and hand one invocation two different answers.
        today = policy.today
        if all_shortlist:
            return engine.preview_all(store, cfg, limit=limit, policy=policy,
                                      profile=profile, today=today)
        if dry_run:
            note, reason = select.select_one(store, lead, cfg, policy)
            if note is None:
                return [engine.PrepResult(lead=lead, status="skipped", reason=reason)]
            pkt = packet.build_packet(note, cfg, profile=profile, today=today,
                                      cv_staged=False)
            return [engine.PrepResult(lead=lead, status="previewed", packet=pkt)]
        return [engine.prep_one(store, cfg, lead, policy, profile=profile, today=today)]

    def record(self, *, lead, ats=None, url=None, dry_run=False):
        """Run the apply sub-app's record step: the never-clobber shortlist ->
        applied transition. Offline, same as `prep` -- just the store."""
        from sluice.apply import engine
        from sluice.apply.config import load_apply_config
        return engine.record_one(self.store(), load_apply_config(), lead,
                                 ats=ats, url=url, dry_run=dry_run)

    def track(self, *, dry_run=False, backend_role="auto", client=None, now_iso=None):
        """Run the track sub-app: fetch Gmail/Calendar since the last run, classify
        and reconcile each message against the vault's in-flight leads. Returns the
        engine's RunReport.

        `client` is a plain constructor argument, not a registered adapter seam:
        unlike store/fetcher/renderer/backend there is exactly one Google client
        shape to resolve against (no config knob selects among providers), so this
        is the test seam rather than a `plugins.get` lookup.

        Save-on-success mirrors cli.py's old cmd_track_run exactly: `_save_seen`
        runs on every non-dry-run call (the seen set is safe to persist even after
        an auth error -- it only ever grew by ids actually processed before the
        break), but `_save_lastrun` is additionally gated on `not rep.auth_error
        and not rep.deadletter_error`: advancing the lastrun watermark past a run
        that never got to classify anything (auth_error), or past a message whose
        dead-letter write failed and so was never persisted (deadletter_error),
        would silently skip that message next time (#49's write-path silent loss)."""
        from datetime import datetime, timezone
        from sluice.track import engine as track_engine
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        from sluice.track.google_client import RealGoogleClient

        tcfg = load_track_config(refuse_relocated_seen_db=True)
        lastrun_path = tcfg.seen_db + ".lastrun"
        seen = _load_seen(tcfg.seen_db)
        deadletter = DeadLetterDb(deadletter_path(tcfg.seen_db))
        since_iso = _load_lastrun(lastrun_path)
        client = client if client is not None else RealGoogleClient(
            tcfg.token_path,
            gmail_max_messages=tcfg.gmail_max_messages,
            calendar_max_events=tcfg.calendar_max_events)
        backend = self.backend(
            backend_role, primary_name=tcfg.primary_backend,
            primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort,
            host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path,
            fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        rep = track_engine.run(self.store(), tcfg, client, backend, seen=seen,
                               deadletter=deadletter, now_iso=now_iso,
                               since_iso=since_iso, dry_run=dry_run)
        if not dry_run:
            _save_seen(tcfg.seen_db, seen)
            # `search_truncated` HOLDS the watermark too. I argued the opposite in review and
            # was wrong on two of three premises, both falsified by measurement:
            #
            #   - "a held window costs a bigger fetch every run" -- false since #137. The cap
            #     is a hard TOTAL across pages, so 400 matches and 50,000 matches both cost
            #     one request. The expense that justified advancing no longer exists.
            #   - "holding loses the same messages as advancing" -- false in the dimension
            #     that decides it. Advancing moves `after:` to TODAY, so every starved message
            #     leaves the addressable set the instant we advance. Holding keeps them
            #     queryable, which is what makes the prescribed remedy (narrow
            #     `gmail_extra_query`) actually recover them on the next run.
            #
            # Gmail returns newest-first, so holding cannot starve NEW mail -- it is always
            # inside the cap. This is not the `deadletter_error` shape, which is a per-message
            # stall that no operator action clears.
            if not (rep.auth_error or rep.deadletter_error or rep.search_truncated):
                _save_lastrun(lastrun_path, now_iso)
        return rep

    def track_confirm(self, *, lead, to, when=None, dry_run=False):
        """Run the track sub-app's confirm step: apply an operator-approved
        proposal (a status advance the engine flagged rather than auto-applied),
        clearing that lead's dead-letter entries on a successful advance."""
        from sluice.track import engine as track_engine
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        tcfg = load_track_config(refuse_relocated_seen_db=True)
        return track_engine.confirm(self.store(), tcfg, lead, to,
                                    deadletter=DeadLetterDb(deadletter_path(tcfg.seen_db)),
                                    when=when, dry_run=dry_run)

    def track_dismiss(self, *, message_id=None, lead=None, dry_run=False):
        """Clear a dead-letter entry a human decided needs no action. `message_id`
        is the only lever for a no-lead entry (a classify-failure or an unmatched
        proposal); `lead` clears a lead's entries without advancing status. A
        dry-run reports the count it would delete without deleting."""
        # The CLI's mutually-exclusive-required argparse group enforces "exactly
        # one of --id/--lead" at that boundary, but a direct caller (test, script,
        # future command) bypasses argparse entirely. Without this guard, both-None
        # falls into clear_lead(None) -> `WHERE lead = NULL` (never true) -> a
        # silent {"cleared": 0} instead of a loud error; both-given makes the
        # dry-run branch count a union of id-or-lead matches while the real branch
        # only ever acts on id (lead is dropped), so the two branches could report
        # different numbers for the same call. Requiring exactly one selector here
        # closes both: the real delete and the dry-run count key on the same
        # single discriminator, so they can never disagree.
        if (message_id is None) == (lead is None):
            raise ValueError("track_dismiss requires exactly one of message_id or lead")
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        tcfg = load_track_config(refuse_relocated_seen_db=True)
        dl = DeadLetterDb(deadletter_path(tcfg.seen_db))
        if dry_run:
            entries = dl.open_entries()
            n = sum(1 for e in entries
                    if (message_id is not None and e.message_id == message_id)
                    or (lead is not None and e.lead == lead))
            return {"cleared": n, "dry_run": True}
        # `status_only=False`: an explicit human dismissal clears EVERYTHING for the lead,
        # which is what the dry-run counter above already counts. The default (status_only)
        # is for the engine's auto-advance clears, where a status change genuinely does not
        # resolve a stale calendar entry or a failed message.
        n = (dl.clear_id(message_id) if message_id is not None
             else dl.clear_lead(lead, status_only=False))
        return {"cleared": n, "dry_run": False}

    def doctor(self, *, offline=False, probe=None):
        """Preflight every configured backend (primary + fallback, per sub-app):
        is the provider known, is a model resolved, are the credentials present
        in THIS process, and -- unless `offline` -- does a one-token round-trip
        succeed? Also preflights everything else a run depends on that a green
        backend table said nothing about: the renderer actually constructs, the
        store's baseline CV, Judging Profile and Candidate Profile (#133/#107 --
        the candidate's identity, checked here rather than as its own separate
        item) are where they should be, track's Google adapter is usable, and
        every preference gate's current posture (abstaining or active).
        Returns a DoctorReport whose `checks` (backends) and `components`
        (everything else) both feed `exit_code`, non-zero when anything
        run-blocking is dead.

        `self.store()` and `self.renderer()` ARE now constructed here -- unlike
        the backend probe, neither performs I/O by being built (a Store's
        `__init__` sets attributes; a Renderer's factory imports its libraries
        and, for `template`, reads a template file, never writes one), and
        `self.fetcher()` (a live Camofox browser) is still never touched, so
        `sluice doctor` still never opens a browser. The store's OPTIONAL
        `preflight()` hook (see core/protocols.py's `Store` docstring) is
        reached via `getattr`, exactly as `cv/engine.py` reaches the renderer
        seam's optional `precheck` -- a store that does not implement it reports
        nothing for that component rather than being treated as broken, and a
        store whose `preflight()` itself raises is reported as the one DEAD row
        that failure is, rather than crashing the one tool that diagnoses a
        broken install. `load_cv_config()` is guarded too, ahead of all of the
        above: a ValueError from it -- today that means `cv.baseline_rel`,
        `cv.render_script` without `cv.renderer`, `cv.compose_timeout`, a
        retired `cv.dossier_dir`, or a legacy `cv.name`/`cv.contact` (#133/#107:
        both moved to the vault's Candidate Profile note) -- becomes one DEAD
        `cv-config` row naming the real error, rather than a traceback out of
        the one command a user runs BECAUSE something -- possibly that very
        config -- is wrong. `cv_cfg` is then `None` for the rest of the run: it
        cannot recover the way store/renderer do, by substituting a bare
        `CvConfig()` and pressing on, because building a sub-app config
        anywhere but its own loader is independently forbidden (see the
        guard's own comment at the call site) -- a table computed off an
        invented default would be the "quiet wrong default" bug class this
        codebase engineers out, aimed at its own diagnostic tool. Only the
        FOUR checks that actually read `cv_cfg` -- cv's own backend targets,
        the renderer, cv's row in the gate-posture sweep, and (#165) the
        negatives-vs-Skills-Inventory cross-check, which sits inside the STORE
        branch but is gated on the same condition -- are skipped;
        the store (including the Candidate Profile row that replaced the old
        cv_cfg-based identity check, #133/#107), track/Google, camofox and
        every other sub-app's gate rows are unrelated to `cv_cfg` and still
        run.

        `probe` is the test seam -- a `callable(backend) -> None` that
        raises `BackendError` on failure; it defaults to the real round-trip.
        The provider is built DIRECTLY via `make_backend` (not the role
        composite), so there is no `FallbackBackend` to disentangle, and it is
        built ONLY when there is something testable -- a known provider whose
        credentials are satisfied -- so a keyless per-token backend is
        classified from config alone, never by catching a construction error."""
        import shutil
        import time

        from sluice.apply.config import load_apply_config
        from sluice.core import doctor as _doctor
        from sluice.core.backends import DEFAULT_MODELS, BackendError, make_backend
        from sluice.core.protocols import RenderDependencyError, RenderError
        from sluice.cv.config import load_cv_config
        from sluice.track.config import load_track_config
        from sluice.triage.config import load_triage_config

        triage_cfg = load_triage_config()
        # `load_cv_config()` already raises ValueError today for several
        # unrelated config mistakes -- `cv.baseline_rel` (moved to the config
        # root), `cv.render_script` set without `cv.renderer`, a non-positive
        # `cv.compose_timeout`, a retired `cv.dossier_dir`, and a legacy
        # `cv.name`/`cv.contact` (#133/#107: both moved to the vault's
        # Candidate Profile note). `doctor` is precisely the command a user
        # runs BECAUSE something about their config is wrong, so an unguarded
        # call here would traceback on the very thing it exists to diagnose;
        # caught here, ahead of the deliberately-guarded
        # self.renderer()/self.store() constructions below. `cv_config_error`
        # is carried as a STRING, not re-raised or
        # re-read from `e`, because the row that reports it is built further
        # down (see the `if cv_config_error is not None:` below) -- keeping
        # the except block narrow to just "catch and remember" avoids two
        # copies of the ComponentCheck construction.
        try:
            cv_cfg = load_cv_config()
            cv_config_error = None
        except ValueError as e:
            cv_cfg = None
            cv_config_error = str(e)
        track_cfg = load_track_config()
        # `cv_cfg` may be None here -- enumerate_targets omits cv's two specs
        # in that case rather than substituting a placeholder (see its own
        # docstring), so triage's and track's backends are still checked.
        targets = _doctor.enumerate_targets(triage_cfg, cv_cfg, track_cfg)
        if probe is None:
            probe = lambda b: b.complete(_doctor.PROBE_PROMPT)  # noqa: E731

        # A provider is usable only if make_backend could actually build it, which
        # takes BOTH guards: the name in DEFAULT_MODELS (its default-model + the
        # unknown-name check) AND a factory registered in the backend seam. Checking
        # the registry is an import + dict lookup, not a round-trip, so --offline can
        # do it too -- and must, or a provider whose plugin is in DEFAULT_MODELS but
        # failed to import would be reported `ok` offline instead of `dead` (live
        # mode catches it via make_backend, but offline skips that). Resolved once.
        registered = set(Sluice.available("backend"))
        checks = []
        for t in targets:
            known = t.provider in DEFAULT_MODELS and t.provider in registered
            needs_key = t.provider in _PROVIDER_ENV
            key_var = _PROVIDER_ENV.get(t.provider, ("", ""))[0]
            api_key, base_url = _provider_creds(t.provider)
            key_present = bool(api_key)
            # A local (no-host) backend that needs no key IS the claude-max CLI, so the
            # binary being on PATH at all is checkable without running anything.
            #
            # NOT gated on `offline` (#243). It used to be, and the consequence was that
            # `--offline` and the default live run disagreed about the SAME fact: offline
            # reported `CLI 'claude' not on PATH` and, since #243, called it SETUP, while a
            # live run skipped this, attempted the probe, and classified the resulting
            # failure as `probe_error` -- DEAD, exit 1. So a fresh install with no `claude`
            # installed got "Broken: triage leads, tailored CVs, track replies" from plain
            # `job-sluice doctor`, which is the exact experience #243 exists to remove, and
            # `--offline` was the only invocation that told the truth. Checking first also
            # saves a subprocess that cannot succeed.
            cli_present = None
            if known and not needs_key and not t.host:
                cli_present = shutil.which(t.claude_path) is not None
            # Round-trip ONLY when live AND buildable+testable: known provider,
            # creds satisfied. Everything else is classified from config alone.
            probe_error = None
            elapsed = None
            # `cli_present is not False`: a missing binary is already classified above and
            # the probe would only rediscover it as a less specific error.
            if (not offline and known and (not needs_key or key_present)
                    and cli_present is not False):
                try:
                    # Deliberately does NOT inherit cv.compose_timeout (#28). That knob
                    # sizes a full CV composition; this is PROBE_PROMPT, a two-token
                    # round trip. Borrowing a raised compose timeout would make `doctor`
                    # -- the command you run BECAUSE something is wrong -- sit on a dead
                    # host for as long as the knob says, which is the opposite of its job.
                    backend = make_backend(
                        t.provider, t.model, api_key=api_key, base_url=base_url,
                        claude_host=t.host, claude_path=t.claude_path)
                    start = time.monotonic()
                    probe(backend)
                    elapsed = time.monotonic() - start
                except BackendError as e:
                    probe_error = str(e)
            check = _doctor.classify(
                t, known=known, needs_key=needs_key, key_present=key_present,
                key_var=key_var, cli_present=cli_present, offline=offline,
                probe_error=probe_error)
            check.elapsed = elapsed
            checks.append(check)

        components = []
        if cv_config_error is not None:
            # Names the REAL failure (`cv_config_error`, whatever field it names)
            # rather than a guessed field -- this row used to hardcode subject
            # "cv.name" unconditionally, which reported a bad `cv.compose_timeout`
            # as if the candidate's NAME were the problem. `blocks=("cv",)` alone,
            # not "apply": apply's packet excludes every cv-only key, so a broken
            # `cv:` block does not stop it. The detail lists exactly the three
            # checks skipped below (see the `if cv_cfg is not None:` guard and
            # the gate-posture sweep further down) -- not "every other check",
            # which would be false: the store (including the Candidate Profile
            # row, #133/#107), track/Google, camofox and every other sub-app's
            # gate rows read nothing off cv_cfg and still run further down.
            components.append(_doctor.ComponentCheck(
                "cv-config", "cv:", _doctor.DEAD,
                f"{cv_config_error} -- cv's backend targets, the renderer, "
                f"cv's gate-posture row and the negatives-vs-Skills-Inventory "
                f"cross-check are skipped this run "
                f"until this is fixed", blocks=("cv",)))

        if cv_cfg is not None:
            # Renderer: construction IS the probe (see classify_renderer). No PDF is
            # written and no backend is called, so this is cheap and safe under
            # --offline too. `plugins.UnknownAdapter` (a misconfigured `cv.renderer`
            # naming no registered renderer) is caught alongside `RenderError` --
            # both are "this seam member could not be built" for doctor's purposes.
            # Measured: a typo'd `cv.renderer` previously crashed the WHOLE command
            # with an uncaught UnknownAdapter, losing the backend checks already
            # computed above -- the opposite of what a diagnostic tool run BECAUSE
            # something is wrong should do.
            #
            # (#243) The SETUP-vs-DEAD split is decided by the exception type the seam
            # DECLARES -- `RenderDependencyError`, "something I need is not installed" --
            # never by matching doctor's message text and never by reaching into
            # `__cause__`. Every other `RenderError`, and an `UnknownAdapter` naming a
            # renderer that does not exist, is a thing the user DID configure and that does
            # not work, and stays DEAD.
            #
            # The first cut asked `isinstance(e.__cause__, ImportError)` and was wrong three
            # ways. It missed the case that actually fires in the field -- weasyprint
            # importing with the extra present but its native libraries absent raises
            # OSError, which `renderers/template.py` calls "the single likeliest real
            # failure" -- so the documented macOS install landed on exit 1 under a heading
            # saying something you configured is broken, on a row whose remedy is `pip
            # install`. Widening that tuple to `(ImportError, OSError)` would have moved the
            # bug rather than fixed it: it mirrors one renderer's `except` clause, so that
            # renderer changing what it catches silently changes doctor's verdict, and it
            # would ALSO have swept in the packaged-template read one layer down, whose own
            # message says "reinstall sluice" and which is a broken install rather than an
            # unfinished setup step. A renderer raising from inside an `except` with no
            # `from` clause (`__cause__` is None -- implicit chaining sets `__context__`)
            # stays misclassified whatever its message says. Letting the renderer declare it
            # fixes all three. `core/protocols.py` carries the argument.
            try:
                self.renderer(cv_cfg)
            except (RenderError, plugins.UnknownAdapter) as e:
                components.append(_doctor.classify_renderer(
                    str(e), missing_dependency=isinstance(e, RenderDependencyError)))
            else:
                components.append(_doctor.classify_renderer(None))

        # Store: the optional preflight() hook, reached the same way
        # cv/engine.py reaches the renderer seam's optional precheck. A store
        # without the hook contributes nothing; a store whose hook raises
        # becomes one DEAD row naming the failure rather than an uncaught
        # exception out of the one command meant to diagnose a broken install.
        # `self.store()` itself is also guarded, against BOTH ways building the
        # vault store can fail: a misconfigured `store:` name raises
        # UnknownAdapter at RESOLUTION (before preflight() is ever reachable),
        # and an invalid `lead_layout` makes `Vault.__init__` raise ValueError
        # (`layout_subfolder`'s own guard, core/leads.py) -- reachable when a
        # `Config` is constructed directly rather than through `load_config()`,
        # which validates `lead_layout` itself and never gets here. Both must
        # be reported the same way rather than crashing -- the identical fix
        # as the renderer's, one layer earlier.
        #
        # `blocks` is the WHOLE pipeline on both rows below, and it is load-bearing rather
        # than decorative (#243): `DoctorReport.verdict()` reads `blocks` to decide which
        # capabilities are still usable, so a DEAD row that names nothing leaves every
        # capability in `Ready now`. Measured -- an unbuildable store printed
        # "Ready now: scrape job boards, triage leads, send applications, track replies"
        # above a `Broken:` row saying the store could not be constructed. A store that
        # will not build is strictly worse than the missing vault `classify_store` already
        # marks as stopping "the entire pipeline, not a subset of it", so it is spelled the
        # same way. `blocks=()` remains meaningful and must not be filled in reflexively:
        # the unreadable `stories` corpus carries it deliberately, because nothing reads
        # that corpus and naming a sub-app there would be an over-claim.
        try:
            store = self.store()
        except (plugins.UnknownAdapter, ValueError) as e:
            components.append(_doctor.ComponentCheck(
                "store", "store", _doctor.DEAD, str(e), blocks=_doctor.ALL_CAPABILITIES))
        else:
            preflight_fn = getattr(store, "preflight", None)
            if preflight_fn is not None:
                try:
                    components.extend(_doctor.classify_store(preflight_fn()))
                except Exception as e:  # noqa: BLE001 -- see the comment above: a
                    # broken preflight must be reported, not crash doctor itself.
                    components.append(_doctor.ComponentCheck(
                        "store", "preflight", _doctor.DEAD, str(e),
                        blocks=_doctor.ALL_CAPABILITIES))
            # #165. Needs BOTH the store and cv_cfg, which is why it lives here and not in
            # `Vault.preflight()` -- whose docstring commits it to COUNTS rather than
            # content, and which is a Store-seam member every implementation would have to
            # grow. The `except` covers only the store READ; the classifier below is pure
            # and sits outside it, so a bug in it surfaces rather than being logged away.
            if cv_cfg is not None:
                try:
                    _skills = store.read_evidence("skills", verified_only=True)
                except Exception as e:  # noqa: BLE001 -- an unreadable corpus is already
                    # reported DEAD by classify_store above WHEN the store implements the
                    # optional preflight hook; when it does not, this line is the only
                    # signal, which is why it is WARNING rather than DEBUG.
                    _log.warning("skills read for the negatives cross-check failed: %s", e)
                else:
                    components.extend(_doctor.classify_negatives_vs_skills(
                        cv_cfg.negatives, _skills))

            # #168 Task 10. A SEPARATE cross-check from the negatives one immediately
            # above -- it needs no cv_cfg at all (the reconciliation is a property of
            # the two evidence corpora alone, not of anything cv-configured), so it
            # runs whether or not the `cv:` block loaded, unlike the `if cv_cfg is not
            # None:` guard above. Both reads sit in ONE try, deliberately unlike that
            # block's single-corpus read: this check needs BOTH corpora to say
            # anything at all (either read failing leaves nothing to reconcile), so
            # splitting them into two try blocks would only add a second near-
            # identical except arm for no behavioural gain. An unreadable corpus is
            # already reported DEAD by classify_store above WHEN the store implements
            # the optional preflight hook, and WARNED here when it does not -- the
            # same shape the negatives cross-check's own except uses.
            try:
                _experience = store.read_evidence("experience", verified_only=True)
                _inventory = store.read_evidence("skills", verified_only=True)
            except Exception as e:  # noqa: BLE001 -- see the comment above.
                _log.warning("evidence read for the skills reconciliation failed: %s", e)
            else:
                components.extend(_doctor.classify_skills_reconciliation(
                    _experience, _inventory))

        # Track/Google: probed through track.google_client's own helper rather
        # than importing the google libs here a second time -- that module is
        # the ONE sanctioned import site (see CLAUDE.md's stdlib-only rule for
        # `sluice/`), so doctor asks it rather than duplicating its imports.
        from sluice.track.google_client import probe_availability

        google_available, google_import_error = probe_availability()
        components.append(_doctor.classify_track_google(
            available=google_available, import_error=google_import_error,
            token_present=os.path.exists(track_cfg.token_path),
            token_path=track_cfg.token_path))

        # Which browser profile an ingest run will drive. Read from the environment, never by
        # constructing a client: `Camofox.__init__` warns on the same misconfiguration this
        # row reports, and doctor saying it twice trains the reader to skim. No network and no
        # browser, so it holds under --offline and keeps the invariant above. (Importing the
        # source registry does scan and import ~22 modules -- filesystem work, not free, and
        # `health_report` already pays it.)
        #
        # ENUMERATED off the registry rather than hand-listed, and gated on the CLASS that
        # actually honours the field: `auth_probe_js` is declared on `BrowserListSource`, and
        # only its `fetch` evaluates it. The gate is kept even though that is currently the
        # only base class in the registry -- a second one could grow the attribute tomorrow
        # and never run it, and doctor would then promise detection that does not happen.
        # (There was such a class until 2026-08-28: `CarouselSource`, retired with its last
        # producer. The hazard it illustrated outlives it.)
        from sluice.core.camofox import resolve_user
        from sluice.ingest import sources as _registry
        from sluice.ingest.base import BrowserListSource

        probe_capable = tuple(
            s.id for s in _registry.all_sources()
            if isinstance(s, BrowserListSource) and getattr(s, "auth_probe_js", None))
        components.append(_doctor.classify_camofox(
            user_env=os.environ.get("CAMOFOX_USER"),
            session_env=os.environ.get("CAMOFOX_SESSION"),
            # THE shared resolver, not a second copy: the two readings disagreed on an
            # exported-but-empty CAMOFOX_USER, so doctor confidently reported a profile the
            # run would not drive.
            resolved_user=resolve_user(),
            probe_capable_sources=probe_capable))

        # Gate posture: enumerated generically over every loaded config's
        # list-typed fields (list_typed_fields), never hand-listed -- the same
        # discipline tests/test_sluice_neutral_defaults.py's identically-shaped
        # sweep applies to the DEFAULTS, applied here to this install's CURRENT
        # values. Most swept fields are preference gates in the #26/#63 sense;
        # a few (Config.dossier_allow_hosts, the noise-word lists) are not --
        # see classify_gate's docstring for why that is fine, since every row
        # is NOTICE for every field that declares a gate_role, which is every field the
        # sweep reaches (#245 made the undeclared case DEGRADED, and that is reachable
        # only from a user's YAML putting a list on a scalar setting).
        # SourceConfig.searches is deliberately excluded: it is a
        # per-source override living inside `sources: {id: {...}}`, not a flat
        # field on one of these instances, so this generic sweep cannot reach
        # it without also loading and iterating the sources dict. NOT reported
        # anywhere else either -- `sluice ingest list-sources` prints
        # enabled/disabled + health, not whether a source still runs its
        # built-in example search. That gap is real and open, not closed by
        # this comment.
        # `cv_cfg` may be None (see above) -- `list_typed_fields` calls
        # `dataclasses.fields(cfg)`, which raises TypeError on None, so it is
        # left OUT of the tuple rather than skipped inside the loop. Inserted
        # at its ORIGINAL triage/cv/track index when present, not appended at
        # the tail: the printed `gates` rows follow this tuple's order, and
        # appending would silently move every `CvConfig.*` row from between
        # `TriageConfig.*` and `TrackConfig.*` to after `ApplyConfig.*` for
        # every install with a perfectly valid `cv:` block -- the identical
        # reasoning `enumerate_targets` states for keeping cv's backend specs
        # in their original position rather than at the tail of ITS list.
        gate_cfgs = (self.config, triage_cfg)
        if cv_cfg is not None:
            gate_cfgs = (*gate_cfgs, cv_cfg)
        gate_cfgs = (*gate_cfgs, track_cfg, load_apply_config())
        for cfg in gate_cfgs:
            owner = type(cfg).__name__
            components.extend(
                _doctor.classify_gate(owner, name, value, role)
                for name, value, role in _doctor.list_typed_fields(cfg))

        # Cached-JD length distribution (Task 8, #169). See classify_dossier_cache's
        # own docstring for why this is a DISTRIBUTION rather than a threshold verdict
        # against `min_jd_chars` -- the short version is that a threshold count would
        # be identically zero at the shipped `min_jd_chars: 0`, exactly the inert-
        # control shape three reviewers rejected on an earlier draft of this task.
        #
        # `_dossier_dir()` never creates the directory (see its own docstring, and
        # `core/paths.py`'s "resolve performs NO WRITES"), so `os.listdir` raising
        # FileNotFoundError is the ordinary fresh-install shape, not an error --
        # nothing has been dossiered yet. Reading it this way (rather than checking
        # os.path.isdir first) keeps the "does not exist" and "cannot be listed"
        # cases (e.g. a dangling symlink, ENOTDIR from a stray same-named file) behind
        # one guard instead of two, and either way this function performs no writes:
        # it must not disarm the #81 relocation notice the way `sqlite3.connect`
        # opening a store file for a mere read once did.
        #
        # Deliberately UNBOUNDED -- no cap, and this is the "say why in a comment"
        # case CLAUDE.md's Task 8 constraint asks for when a scan is not bounded. A
        # cache entry exists per DISTINCT lead ever dossiered (keyed by DossierCache.
        # cache_key), never per status transition or per re-scrape of the same lead --
        # unlike the full lead-note walk `Vault.preflight()`'s contract forbids it from
        # doing (core/protocols.py), which parses YAML frontmatter across every active,
        # applied and archived note. A few thousand small JSON reads costs low tens of
        # milliseconds, well inside what a preflight users run often and cheaply should
        # cost; if a real deployment ever grows large enough for that to stop holding,
        # the fix is a bound reported IN the detail string (never a silent truncation --
        # a capped count reads as a complete one), not a silent skip.
        # The SCAN itself lives on DossierCache (`census`), which owns the on-disk
        # layout it reads and the `jd.markdown` extraction it measures. Inlined here it
        # hardcoded both: a change to the cache's naming scheme would have left this
        # counting ZERO and reporting "no cached dossiers yet" -- indistinguishable from
        # a fresh install -- and it re-derived the extraction `jd_arrived` calls itself
        # the sole owner of. Constructed with ttl/fetcher/floor it will not use, because
        # a census reads what is on disk and asks nothing of them.
        # ttl and floor are passed as 0: a census reads what is on disk and asks nothing
        # of either, and passing a REAL floor here would be worse than meaningless -- it
        # would look like the distribution depended on the operator's `min_jd_chars`,
        # which is exactly the independence this row exists to give them.
        dossier_counts = self.dossier_cache(self._dossier_dir(), 0, 0).census()
        components.append(_doctor.classify_dossier_cache(dossier_counts))

        return _doctor.DoctorReport(checks=checks, components=components)

    # ── introspection ────────────────────────────────────────────────────────
    @staticmethod
    def available(seam: str) -> list:
        _import_plugins(seam)
        return plugins.available(seam)


def _import_plugins(seam: str) -> None:
    """Import the package whose modules register under `seam`."""
    if seam == _STORE_SEAM:
        import sluice.stores  # noqa: F401  (import triggers registration)
    elif seam == _FETCHER_SEAM:
        import sluice.fetchers  # noqa: F401
    elif seam == _RENDERER_SEAM:
        import sluice.renderers  # noqa: F401
    elif seam == _BACKEND_SEAM:
        import sluice.backends  # noqa: F401
    else:
        raise plugins.UnknownAdapter(
            "seam", seam,
            [_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM, _BACKEND_SEAM])
