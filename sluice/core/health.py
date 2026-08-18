"""Per-source health: run history, a drift detector, and an auto-retire rule.

Source drift (a site moving/renaming/DOM-changing) is a dominant scanner failure mode, but
NOT the only one -- on 2026-08-15 a single wrong browser-profile setting produced eight-plus
zero-yield runs across three sources, and the module was built on the assumption that could
not happen. The engine records each source's yield + signals here, asks detect_drift whether
this run looks wrong relative to the source's own baseline, and retires a source that has
produced nothing, FOR NO REASON WE CAN NAME, several runs running.

Two things to understand about `should_retire` before changing it:

WHAT RETIREMENT ACTUALLY DOES. `ingest/engine.py` sets `source.enabled = False` on the
in-memory registry, and that is never persisted -- `cli._save_disabled` is reached only from
`ingest enable`/`disable`, and `_is_enabled` re-reads `getattr(src, "enabled", True)`, which
is True again in the next process. So retirement does NOT stop a source running tomorrow. Its
real value is as the only CUMULATIVE, DURABLE signal the system has: the `RETIRE` flag in
`ingest list-sources --health`. Anything that suppresses retirement must therefore replace
that signal, or it trades a loud wrong answer for a quiet permanent one -- see
`explained_streak`, which exists for exactly that reason.

RECOVERABLE VS NOT. Suppressing retirement is right only when an operator action brings the
source back (an expired login, a rate-limit, a browser server that is down). It is wrong when
the site has MOVED: the evidence never changes, and this repo's entire auto-retire history is
that case (`sources/hired.py`, `sources/hackajob.py`, both retired by hand after a redirect).
"""
import json
import os
from statistics import median

from sluice.core.paths import resolve

# The content-completeness signals a producer may compute over a search's PARSED leads
# (#156) -- `ingest/engine.py`'s `_lead_rates`. Named here, not there, so `record`'s sticky
# high-water update and `detect_drift`'s `blank` check both consume the SAME roster rather
# than each hand-listing the two keys and drifting apart.
RATE_SIGNALS = ("company_rate", "link_rate")


class HealthStore:
    """JSON-backed per-source run history. One file, whole-object rewrite -
    the data is tiny (a handful of sources, last ~30 runs each)."""

    _KEEP = 30  # cap history per source

    def __init__(self, path: str | None = None):
        # Same pattern as SeenDb: an explicit path wins, otherwise resolution decides
        # (env var, then the per-system state root). This is the ONE place that lives
        # -- app.py's ingest() and cli.py's cmd_health/cmd_list_sources all construct
        # HealthStore() bare and get the same path, so the file `ingest` writes is
        # always the file `health` reads.
        #
        # `path or resolve(...)` and not the other order: an explicit constructor
        # argument must beat the environment, or every `HealthStore(str(tmp_path/...))`
        # in the suite would retarget a developer's real file and stay green while
        # doing it. It also means `resolve` is not called at all when a caller names a
        # path, so an explicit caller can never trip the migration warning.
        self.path = path or resolve(env_var="SLUICE_HEALTH", config_value="",
                                    kind="state", name="sluice_health.json")
        self._data = self._load()

    def _load(self) -> dict:
        # SILENT on any failure, and that is the right tier for this file (see
        # docs/ARCHITECTURE.md): run history is DERIVED telemetry that rebuilds itself
        # on the next run, so a wrong answer costs a drift-detection baseline rather
        # than data. `ingest/engine.py` rules the same way on the write side. Do not
        # copy this into a store whose empty read gets written back as truth.
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def record(self, source_id: str, count: int, signals: dict | None = None) -> None:
        signals = signals or {}
        entry = self._data.setdefault(source_id, {"runs": []})
        runs = entry["runs"]
        runs.append({"count": count, "signals": signals})
        entry["runs"] = runs[-self._KEEP:]
        # The STICKY high-water (#156), updated here rather than derived from `runs` --
        # deliberately a SEPARATE field, not folded into the capped rolling window. A
        # source that rots and stays rotted would otherwise fire `blank` for exactly
        # `_KEEP` consecutive runs and then go PERMANENTLY silent once its last healthy
        # run scrolls out of the window (measured: ~10 days at 3 runs/day, shorter than
        # any of #156's four incidents lasted). `max(stored, this_run)` never decreases,
        # so no reset is needed: once a board is genuinely fixed, its next good run simply
        # stops being "low" relative to the (unchanged or now-higher) high-water, and
        # `blank` stops firing on its own. Gated on `count > 0`: a zero run carries no
        # rate to have been high OR low.
        if count > 0:
            hw = entry.setdefault("rate_high_water", {})
            for key in RATE_SIGNALS:
                if key in signals:
                    hw[key] = max(hw.get(key, 0.0), signals[key])
        self._save()

    def counts(self, source_id: str, n: int = 7) -> list:
        runs = self._data.get(source_id, {}).get("runs", [])
        return [r["count"] for r in runs[-n:]]

    def baseline(self, source_id: str) -> float:
        """Median of the last 7 run counts - robust to the odd bumper/empty run."""
        counts = self.counts(source_id, 7)
        return float(median(counts)) if counts else 0.0

    def rate_highs(self, source_id: str) -> dict:
        """{signal: sticky high-water}, read BEFORE this run's `record()` call -- the same
        timing discipline as `baseline`. Absent when there is no history for that signal at
        all: an accessor that read a missing key as `0.0` would make a source's own FIRST
        rotted run look like a collapse relative to nothing, which is the health-store
        analogue of empty-config-abstains."""
        return dict(self._data.get(source_id, {}).get("rate_high_water", {}))

    def prior_rate(self, source_id: str, key: str) -> float | None:
        """The most recently RECORDED run's value for `key`, or `None` if there is no
        prior run or it carried no such signal. Read before `record()`, same as
        `rate_highs`/`baseline` -- this is the "was the run BEFORE this one also low"
        half of `blank`'s 2-consecutive-run streak requirement."""
        runs = self._data.get(source_id, {}).get("runs", [])
        if not runs:
            return None
        return runs[-1].get("signals", {}).get(key)

    def should_retire(self, source_id: str, threshold: int = 3) -> bool:
        """True once the last `threshold` runs are all dead -- produced nothing for a reason
        we cannot name, or one no operator action will undo. See `_is_dead`."""
        runs = self._data.get(source_id, {}).get("runs", [])
        if len(runs) < threshold:
            return False
        return all(_is_dead(r) for r in runs[-threshold:])

    def explained_streak(self, source_id: str) -> tuple:
        """`(reason, n)` for a source stuck on the SAME named failure -- `(None, 0)` otherwise.

        This is what makes suppressing retirement safe. Retirement's real value was never
        stopping the source (it does not persist -- see the module docstring); it was being the
        one CUMULATIVE signal an operator could see days later. Exempting explained failures
        without this would swap a loud wrong answer for a silent permanent one: the 2026-08-15
        sources would sit at `baseline=0` with no RETIRE flag and nothing saying why.

        Reads the signals `record` already persists, so it costs no new state. Counts back only
        while the reason is UNCHANGED: a source that flips auth -> redirect -> auth is a
        different, noisier problem than one wedged on the same thing since Tuesday."""
        runs = self._data.get(source_id, {}).get("runs", [])
        reason, n = None, 0
        for run in reversed(runs):
            if run.get("count", 0) != 0:
                break
            this = _explained(run.get("signals", {}) or {})
            if this is None or (reason is not None and this != reason):
                break
            reason, n = this, n + 1
        return (reason, n) if reason else (None, 0)


# The PERSISTENCE list: the signal keys `ingest/engine` keeps sticky across a source's
# searches, because a reason found on search 1 must not be overwritten by search 3's honest
# zero. That is its only consumer -- `_explained` does not read this tuple.
#
# Membership here is deliberately NOT the same set as `_explained` classifies on, in both
# directions, so do not "align" them:
#
#   - BROADER: `auth_probe_error` persists but never explains. A broken probe must stay
#     visible (otherwise the guard silently disables itself) without deferring retirement,
#     which would keep a genuinely dead source alive. The reason lives with the producer, in
#     `ingest/base.py`'s `health_hint`.
#   - NARROWER: `_explained` derives `redirect` from `requested_host`/`landed_host`, and (#156)
#     `login` from `requested_path`/`landed_path` -- both pairs absent here on purpose. Each is
#     a MATCHED PAIR describing one search, and the merge is `{**explained, **signals}` -- so
#     persisting either pair independently could pair search 1's requested half with search 3's
#     landed half and report a redirect or a login wall that never happened. The cost is that a
#     redirect/login wall on an early search does not survive a later clean one; a phantom one
#     on every multi-search source would be worse.
EXPLAINING_SIGNALS = ("fetch_error", "blocked", "auth", "auth_probe_error")


def _dewww(host: str) -> str:
    """`host` with a leading `www.` removed -- ONE prefix strip, not a registrable-domain
    parse. Named for what it does: an earlier `_registrable` promised eTLD+1, which would have
    misled the next reader to pass it `jobs.80000hours.org` expecting `80000hours.org`.

    An apex -> `www` redirect is normal, permanent and benign, and nine registered sources
    request an apex host (cord.com, hired.com, remoteok.com, wellfound.com, ...). Treating
    that hop as a "redirect" would report drift on every single run for nine sources that are
    working perfectly."""
    return host[4:] if host.startswith("www.") else host


# The vocabulary a landed-URL SEGMENT must match for `_login_segment` to call it a login
# wall (#156). A pinned module constant, not a config knob (see docs/ARCHITECTURE.md): a
# test asserts this exact set, since widening it weakens auto-retire and must be a
# deliberate change, not a drive-by edit.
#
# Measured against all 22 shipped sources' own search URLs (none contains any of these
# words in its own path) and 16 plausible healthy/incident landings before settling on
# this set. `account` is DELIBERATELY excluded: it was the one word that produced a
# plausible-healthy false positive under every matching strategy tried (`/account/jobs`
# is a legitimate results page on more than one board), and it costs nothing -- incident
# 3 landed on `/login`, never `/account`. `authwall`/`sessions`/`oauth`/`sso` are
# explicit entries rather than left to the prefix rule below to reach from `auth`/
# `session`, because the boundary check in `_login_segment` deliberately refuses to
# reach them from those shorter words (that refusal is what keeps `/author/...` and a
# coding-challenge platform's `/challenges/search` safe).
_LOGIN_SEGMENTS = frozenset({
    "login", "signin", "sign-in", "signon", "logon",
    "auth", "authwall", "authenticate", "oauth", "sso",
    "session", "sessions",
    "challenge", "captcha", "verify", "register", "onboarding", "2fa", "mfa",
})


def _login_segment(path: str) -> str | None:
    """The first `_LOGIN_SEGMENTS` word matched by a `/`-separated segment of `path`, or
    `None`. `_` is normalised to `-` first, so Devise's `/users/sign_in` reaches
    `sign-in`.

    PREFIX matching with a non-alphanumeric BOUNDARY, not exact-segment and not
    substring -- both were measured and rejected. Exact-segment misses real
    interstitials: LinkedIn's actual logged-out target is `/authwall`, and Cloudflare's
    challenge page is `/cdn-cgi/challenge-platform/...`. Naive substring matches
    `/author/...` (contains `auth`) and a coding-challenge platform's own
    `/challenges/search` (contains `challenge`) -- both real, both false positives.
    A prefix match with a boundary catches the first pair and excludes the second:
    `authwall` starts with `auth` at a boundary (end of string), `challenge-platform`
    starts with `challenge` at a boundary (`-`), but `author` and `challenges` do not
    (the next character, `o`/`s`, is alphanumeric)."""
    for segment in path.lower().replace("_", "-").split("/"):
        for word in _LOGIN_SEGMENTS:
            if segment == word or (
                segment.startswith(word) and not segment[len(word):len(word) + 1].isalnum()
            ):
                return word
    return None


def _login_wall(requested_path: str, landed_path: str) -> bool:
    """True iff the LANDED path carries a login-vocabulary segment the REQUESTED path did
    not ask for. No query-string matching: measured false positives on an ordinary
    `?q=account+manager` search and on a landed URL merely gaining a `session_id=` param
    on a healthy redirect -- incident 3's evidence (`/login?redirect=%2F`) is fully caught
    by the path alone, so the query string buys nothing and costs those two.

    The "did not ask for" half is the empty-config-abstain case: a source whose configured
    `sources.<id>.searches` legitimately points at a login-shaped path (the same word
    matched on both sides) must not report a permanent false positive against its own
    request."""
    word = _login_segment(landed_path or "")
    return word is not None and word != _login_segment(requested_path or "")


def _explained(signals: dict) -> str | None:
    """Why this run looks wrong, when we can say -- `None` when we cannot.

    THE one definition of "we know what went wrong", shared by `detect_drift` (which reports
    it) and `_is_dead` (which defers retirement). Two copies would drift. Note this is NOT a
    past disagreement being repaired: before 2026-08-15 neither function had the concept at
    all, so they agreed by having the same blind spot. Centralising it is what stops the next
    reason being added to one and not the other.

    An `error` is deliberately NOT an explanation. It says the fetch blew up, not that the
    page told us something -- there is nothing on the site to go and fix, so an erroring
    source that also yielded nothing should still retire.

    CAUTION when adding a reason here, and know that there is NO backstop. A reason in
    `_RECOVERABLE` defers retirement indefinitely: `should_retire` needs `threshold`
    consecutive dead runs and an explained run is never dead, so the counter never
    accumulates -- a source stuck on `auth` for 300 runs never retires. That is deliberate
    (the fix is an operator action, and `explained_streak` is what makes the wait visible),
    but it means a reason that fires benignly buys a dead source unlimited time. `_dewww`
    exists because `redirect` nearly was such a reason."""
    # FIRST, because it explains every other signal's absence: if the browser never gave us a
    # tab, or the page evaluate failed, we did not look at the site at all. Discarding this was
    # how a single Camofox outage could record a bare `zero` for all ~23 sources at once and
    # retire the lot -- the clearest "could not read" in the system, thrown away one layer
    # before the classifier saw it.
    if signals.get("fetch_error"):
        return "unreachable"
    requested, landed = signals.get("requested_host"), signals.get("landed_host")
    if requested and landed and _dewww(requested) != _dewww(landed):
        return "redirect"
    # AFTER redirect, BEFORE blocked (#156): a board that redirects cross-host to a login
    # page is more likely a genuine relocation than a login wall, so `redirect` -- which
    # does not defer retirement either, see `_RECOVERABLE` below -- gets first say. A
    # same-host auth wall (incident 3/4: `/jobs?query=...` -> `/login?redirect=%2F`, host
    # unchanged) never reaches the redirect branch above, so ordering costs it nothing.
    if _login_wall(signals.get("requested_path", ""), signals.get("landed_path", "")):
        return "login"
    if signals.get("blocked"):
        return "blocked"
    if signals.get("auth"):
        return "auth"
    return None


# Explanations an OPERATOR ACTION can undo, and which therefore defer retirement. The
# distinction is whether the run is evidence or a corpse: an expired login or a rate-limit
# comes back once someone fixes the config, so killing the source deletes the very signal that
# would prompt the fix. A `redirect` does not come back -- the board has MOVED, and the
# evidence never changes no matter how many more times we look.
#
# This repo's entire auto-retire history is the redirect case: `sources/hired.py` (hired.com
# 302s to lhh.com after the LHH acquisition) and `sources/hackajob.py` (hackajob.co/search ->
# a 404 page). Both were retired BY HAND. Treating redirect as recoverable would mean the
# automatic rule could never reach that conclusion again, and a relocated board would burn a
# browser slot on every run forever -- which is the exact waste `should_retire` exists to stop.
#
# `login` (#156) belongs beside `redirect`, NOT beside `auth`, and it is easy to get this
# backwards -- an expired login sounds like the recoverable case. It is not membership here
# that matters for the incident it was built for: `_is_dead` short-circuits on `count == 0`,
# and incident 4's login-walled run returned rows (count 5), so `_RECOVERABLE` membership was
# never consulted for it either way. Membership only matters for a ZERO-count run landing on
# a login path, and there the evidence is identical to `redirect`'s: the board never comes
# back on its own, an operator has to notice and act, and this repo's real auto-retire
# history is exactly that shape (hired.com, hackajob.co). Including `login` here would grant
# a PERMANENTLY paywalled board unlimited life -- the specific hazard `_explained`'s own
# docstring warns about ("a reason that fires benignly buys a dead source unlimited time").
# The genuinely-recoverable case (an expired session) is already served by `auth`, backed by
# a probe that CONFIRMS the logged-out state rather than inferring it from a URL.
_RECOVERABLE = ("auth", "blocked", "unreachable")


def _is_dead(run: dict) -> bool:
    """Dead = produced nothing AND we cannot say why. An outright error is not a "why", so an
    errored zero IS dead -- but an errored run that still returned rows is not. Nor is a
    reason no operator action would undo: a relocated board is dead however well we can
    explain it.

    A source we could not READ because of a fixable condition is BROKEN, not dead, and
    retiring it deletes the evidence: it stops running, so it stops reporting the auth/block
    failure, so nobody ever learns what to fix. That is precisely how a wrong `CAMOFOX_USER`
    cost three heavyweight sources for eight-plus runs -- the retirement looked like the
    system working.

    A relocated board is the opposite case, and `detect_drift` still REPORTS `redirect` for
    it, so nothing is lost from the run report by retiring it."""
    signals = run.get("signals", {}) or {}
    # No `error` short-circuit. It would be redundant AND wrong. Redundant because `error` is
    # deliberately not an explanation, so a zero-yield error already falls through to dead
    # below. Wrong because `error` is NOT in `EXPLAINING_SIGNALS` and so is not made sticky by
    # `_run_source` -- it survives only from the LAST search, which means a source whose final
    # search errored while earlier ones returned rows carries both a positive count and an
    # error. A source that just returned rows is not dead.
    return run.get("count", 0) == 0 and _explained(signals) not in _RECOVERABLE


# `blank`'s two gates (#156), each measured against the real 22 sources + 16 golden fixtures
# before settling here -- neither is the naive first guess:
#   - HIGH-WATER FLOOR 0.8, not 0.5: at 0.5, naukrigulf's raw company_rate (0.385, before
#     `_lead_rates` moved this to PARSED leads) sat close enough that the max of 30 draws
#     routinely crossed it, producing a false alarm on ~62% of healthy 30-run windows. 0.8
#     costs zero detection latency on the incident-1 shape.
#   - COLLAPSE RATIO 0.4, reused from `drop` rather than invented fresh -- tightening it
#     (e.g. to 0.25) was measured and made naukrigulf/wttj's false-positive rates WORSE, not
#     better, because the real fix is the floor and the streak below, not a tighter ratio.
_BLANK_HW_MIN = 0.8
_BLANK_COLLAPSE = 0.4


def _blank_reason(signals: dict, rate_highs: dict | None, rate_priors: dict | None) -> bool:
    """True iff this run shows a company/link completeness COLLAPSE relative to the
    source's own sticky high-water, sustained across the last TWO recorded runs.

    The 2-consecutive-run requirement is the third gate, alongside the row floor
    (`ingest/engine.py`'s `_lead_rates`, which withholds the rate keys entirely below 8
    parsed leads) and the 0.8 high-water floor above. It costs exactly one run of
    detection latency and was what took every measured false-positive case (naukrigulf,
    wttj) to approximately zero -- a single low run is noise on a small sample; two in a
    row is a source that stopped recovering.

    `rate_highs`/`rate_priors` are read by the CALLER before `record()` -- see
    `HealthStore.rate_highs`/`prior_rate` -- so this function stays pure, matching
    `baseline`'s existing calling convention."""
    for key in RATE_SIGNALS:
        hw = (rate_highs or {}).get(key)
        rate = signals.get(key)
        if hw is None or rate is None or hw < _BLANK_HW_MIN or rate >= _BLANK_COLLAPSE * hw:
            continue
        prior = (rate_priors or {}).get(key)
        if prior is not None and prior < _BLANK_COLLAPSE * hw:
            return True
    return False


def detect_drift(
    source_id: str, count: int, signals: dict | None, baseline: float,
    *, rate_highs: dict | None = None, rate_priors: dict | None = None,
) -> str | None:
    """Classify this run against the source's baseline. Returns the reason, or None if healthy.

    Precedence: an EXPLAINED failure (redirect > login > blocked > auth) outranks a bare
    `zero`, and `zero` outranks `drop`. The explanation is checked FIRST on purpose. Testing
    `count == 0` first -- as this did until 2026-08-15 -- discards the redirect/blocked
    signals the caller already gathered and collapses every distinct failure into the one
    word that cannot be acted on. Within the count>0 arm the full order is
    login/redirect/blocked > fallback > blank > drop -- direct producer evidence
    (`fallback`) outranks an inferred one (`blank`), and both outrank the bare row-count
    comparison (`drop`), because a shape-level signal names the actionable cause.

    Two separate justifications, kept separate on purpose: the precedence reversal is right on
    general principle, but it is NOT what would have rescued the 2026-08-15 LinkedIn case.
    Logged-out LinkedIn serves guest markup at the SAME url, so there was no redirect signal
    to discard -- only the new `auth` probe surfaces that one.

    `rate_highs`/`rate_priors` are keyword-only and default to `None` (treated as "no
    history") rather than being folded into `signals`: they describe HISTORY the caller
    already holds (`HealthStore.rate_highs`/`prior_rate`), not this run's own measurement,
    and every existing positional call site in the suite stays valid unchanged."""
    signals = signals or {}
    reason = _explained(signals)
    if count == 0:
        # The explanation replaces "zero", which is the one classification nobody can act on.
        return reason or "zero"
    # The run PRODUCED rows, so it is not a failure to explain. Only the reasons that stay
    # interesting alongside a successful fetch survive here -- `redirect`/`blocked` were here
    # before. `login` joins them (#156): a login wall that still returns rows (a five-row page
    # of chrome, incidents 3/4) is exactly the shape a bare count check cannot see. `auth`/
    # `unreachable` deliberately do NOT join: gating them on count would let a 200-row run
    # report drift and fire a notification off one search's stale signal.
    if reason in ("redirect", "login", "blocked"):
        return reason
    # Direct producer evidence outranks an inferred rate collapse (#156): a row the
    # extractor's own fallback stamped IS the explanation, not a hint toward one -- see
    # `_first_degraded` in `ingest/base.py`. Checked ABOVE `drop` for that reason: when a
    # count collapse and a stamped fallback coincide, the fallback names the actionable cause.
    if signals.get("degraded"):
        return "fallback"
    # An INFERRED completeness collapse (#156), checked above `drop` for the identical
    # reason: incident 1's actual harm was ~185 blank-companied leads at a count that looked
    # perfectly healthy, so a content-shape signal must outrank a bare row-count comparison
    # whenever both are available.
    if _blank_reason(signals, rate_highs, rate_priors):
        return "blank"
    if baseline and count < 0.4 * baseline:
        return "drop"
    return None
