"""sluice doctor: prove the pipeline is actually usable, not just configured.

Pure, zero-I/O core. The impure half -- resolving creds, building a provider,
running a one-token round-trip, constructing (but never writing through) the
store and renderer seams -- lives in `Sluice.doctor` (core/app.py); the
formatting and exit-code plumbing live in `cli.py`. This module owns only the
rules: what is configured (enumeration), and given a set of resolved facts
about one piece of it, is it ok / degraded / dead / worth a notice
(classification).

Backend classification is ROLE-AWARE, and that is the whole point. The default
install ships a keyless `deepseek` fallback, which `_make_fallback` already
treats as a sanctioned degrade to primary-only -- so a keyless *fallback* is
`degraded` (exit 0), while a keyless *primary* (a run cannot happen) is `dead`.
A backend whose credentials ARE present but whose round-trip fails is `dead`
regardless of role: that is the silently-non-functional fallback this tool
exists to catch -- the one you believe in and never test until the primary
dies.

Backends were the only thing doctor probed for a while, which let the renderer,
the store artefacts and every preference gate stay invisible: `2 ok, 1
degraded` was reported by a real install whose renderer could not construct
(WeasyPrint's native libraries were not on the dynamic linker's path) and whose
`cv.contact` was blank, so a composed CV would have rendered with no way to
reach the candidate. Nothing above probed for either, because nothing did.
`ComponentCheck` below is the second table this module now classifies, one row
per non-backend piece a run depends on.
"""
from dataclasses import dataclass, field, fields

from sluice.core.backends import option_like
from sluice.core.camofox import profile_dir as camofox_profile_dir
# The bucket boundaries this module LABELS and DossierCache.census COUNTS with -- one
# home, so a moved boundary cannot leave the label asserting the old number.
from sluice.core.dossier import JD_LENGTH_BUCKETS as _JD_LENGTH_BUCKETS

# Four states, as bare strings so callers (cli formatter, exit_code) and tests
# share one vocabulary without importing an enum. NOTICE is not a severity --
# see DoctorReport.exit_code for why it is excluded from the count rather than
# folded in as the mildest DEGRADED.
OK = "ok"
DEGRADED = "degraded"
DEAD = "dead"
NOTICE = "notice"

# The round-trip prompt. Tiny on purpose -- a per-token backend costs a token or
# two to answer it, and the answer is discarded (only "did complete() raise?"
# matters). Deliberately NOT paired with a tight max_tokens cap: the
# OpenAI-compatible backend treats finish_reason=length as a hard error, so
# capping the completion would manufacture a false `dead`.
PROBE_PROMPT = "Reply with the single word: ok"


@dataclass(frozen=True)
class RoleUse:
    """One (sub-app, role) pair that references a backend target. A single
    target can be referenced by several -- e.g. the shared deepseek fallback is
    used by triage, cv, and track."""
    subapp: str
    role: str  # "primary" | "fallback"


@dataclass
class BackendTarget:
    """One distinct configured backend, after deduping identical
    (provider, model, host, claude_path) across sub-apps and roles. `claude_path`
    is only meaningful for the claude-max CLI; `host` is "" for a local backend."""
    provider: str
    model: str
    host: str
    claude_path: str
    uses: list = field(default_factory=list)  # list[RoleUse]

    @property
    def is_primary(self) -> bool:
        """True if ANY use is a primary. A backend that serves as a primary
        anywhere must satisfy the strict primary rule (a keyless primary is
        dead), even if it is also used as a fallback elsewhere."""
        return any(u.role == "primary" for u in self.uses)


@dataclass
class BackendCheck:
    target: BackendTarget
    state: str
    detail: str
    elapsed: float | None = None  # round-trip seconds, when one was run


@dataclass(frozen=True)
class ComponentCheck:
    """One non-backend fact `sluice doctor` reports on: the renderer, the
    store's on-disk artefacts (which include the Candidate Profile note's own
    identity check, #133/#107), track's Google adapter, or one preference
    gate's posture.

    `component` groups rows in the printed report ("renderer", "store",
    "track", "camofox", "gates"); `subject` names the specific thing
    checked within that group ("cv.renderer", "Candidate Profile",
    "TriageConfig.accept_titles", ...). `blocks` names the sub-apps this
    specific failure stops -- the ComponentCheck analogue of BackendTarget.uses
    -- so the printed detail can say what a DEAD/DEGRADED row actually costs
    rather than leaving the reader to infer it."""
    component: str
    subject: str
    state: str
    detail: str
    blocks: tuple = ()


@dataclass
class DoctorReport:
    checks: list  # list[BackendCheck]
    components: list = field(default_factory=list)  # list[ComponentCheck]

    def exit_code(self, *, strict: bool = False) -> int:
        """Non-zero iff a run-blocking backend or component is dead. `--strict`
        additionally fails on any degraded one (the cron mode that enforces a
        believed-in fallback).

        NOTICE never contributes, under `--strict` or otherwise -- that
        exclusion is BY CONSTRUCTION (the states this loop tests for), not a
        filter applied to a wider set, so it cannot be silently dropped by
        deleting one `if`. This is the same posture #26/#63 already state for
        an empty preference gate: abstaining is the shipped default and
        legitimate, so a fresh, wholly unconfigured install must exit 0. If a
        NOTICE affected the exit code, `--strict` in a cron job would fail on
        every install that has not opted into every optional gate -- the
        672ad2a class one level up, this time aimed at the tool's own exit
        status rather than at a lead."""
        states = [c.state for c in self.checks] + [c.state for c in self.components]
        if DEAD in states:
            return 1
        if strict and DEGRADED in states:
            return 1
        return 0


def _fallback_host_path(fallback_backend, host, claude_path):
    """(#117) `_make_fallback`/`_make_fallback_strict` forward host/claude_path to
    EVERY fallback build now, unconditionally -- but they are only MEANINGFUL when
    the fallback provider actually IS claude-max; every other factory ignores them.
    Folding them into the dedup key regardless would needlessly split two sub-apps'
    otherwise-identical per-token fallback (the common case) into separate probes, so
    this mirrors runtime behaviour rather than the raw plumbing: real host/path when
    claude-max plays fallback, the shared "", "claude" default otherwise."""
    return (host, claude_path) if fallback_backend == "claude-max" else ("", "claude")


def enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list:
    """Every sub-app × role backend, deduped by (provider, model, host, claude_path).

    Apply is absent: it is offline by contract and has no backend. The fallback leg's
    host/claude_path come from `_fallback_host_path` above -- real values when the
    fallback provider IS claude-max (#117: a remote-host install may name claude-max
    as either role, and `Sluice.backend()` threads the same config to both legs), the
    shared "", "claude" default otherwise, so doctor probes what a real run actually
    builds either way.

    Effort is deliberately NOT part of the dedup key: it changes cost/quality,
    not whether the backend works, so triage(medium)+cv(max) fold into one
    claude-max probe. A per-sub-app MODEL override does split, preserving the
    per-sub-app "is this a live model id" check. `claude_path` IS in the key so
    two claude-max backends pointing at different binaries never collapse.

    `cv_cfg` may be `None` -- `Sluice.doctor` passes that when `load_cv_config()`
    itself raised (#133/#107). cv's two specs are simply OMITTED from the
    enumeration then, rather than substituted with a placeholder: triage's and
    track's backends are unrelated to cv's config and must still be checked, and
    the alternative -- building a bare `CvConfig()` here to read fields off --
    is exactly what tests/test_config_paths.py's
    test_no_production_code_builds_a_sub_app_config_directly forbids anywhere
    outside a sub-app's own loader.
    """
    specs = [
        # (subapp, role, provider, model, host, claude_path)
        ("triage", "primary", triage_cfg.primary_backend, triage_cfg.claude_max_model,
         triage_cfg.claude_max_host, triage_cfg.claude_max_path),
        ("triage", "fallback", triage_cfg.fallback_backend, triage_cfg.cheap_model,
         *_fallback_host_path(triage_cfg.fallback_backend, triage_cfg.claude_max_host,
                              triage_cfg.claude_max_path)),
    ]
    if cv_cfg is not None:
        # Kept in its ORIGINAL triage/cv/track position rather than appended at the
        # end: a shared target's `uses` list is built in spec-iteration order, and
        # `format_roles` prints subapps in that same order -- moving cv to the tail
        # would silently reorder "primary: triage, cv, track" to "..., track, cv"
        # for every install that shares one backend across all three, with no
        # behavioural reason tied to the None case this branch exists for.
        specs.append(("cv", "primary", cv_cfg.primary_backend, cv_cfg.compose_model,
                      cv_cfg.compose_host, cv_cfg.compose_claude_path))
        specs.append(("cv", "fallback", cv_cfg.fallback_backend, cv_cfg.cheap_model,
                      *_fallback_host_path(cv_cfg.fallback_backend, cv_cfg.compose_host,
                                           cv_cfg.compose_claude_path)))
    specs += [
        ("track", "primary", track_cfg.primary_backend, track_cfg.claude_max_model,
         track_cfg.claude_max_host, track_cfg.claude_max_path),
        ("track", "fallback", track_cfg.fallback_backend, track_cfg.cheap_model,
         *_fallback_host_path(track_cfg.fallback_backend, track_cfg.claude_max_host,
                              track_cfg.claude_max_path)),
    ]
    by_key: dict = {}  # (provider, model, host, claude_path) -> BackendTarget, insertion-ordered
    for subapp, role, provider, model, host, claude_path in specs:
        # claude_path is IN the key (rev-001): two claude-max backends that share
        # provider/model/host but shell different `claude` binaries are genuinely
        # different checks -- collapsing them would probe only the first path and
        # report a false `ok` for the second. For a per-token backend claude_path
        # is the harmless "claude" default and does not over-split.
        key = (provider, model, host, claude_path)
        target = by_key.get(key)
        if target is None:
            target = BackendTarget(provider=provider, model=model, host=host,
                                   claude_path=claude_path)
            by_key[key] = target
        target.uses.append(RoleUse(subapp, role))
    return list(by_key.values())


def classify(target, *, known, needs_key, key_present, key_var, cli_present,
             offline, probe_error) -> BackendCheck:
    """The rules table, as a pure function of already-resolved facts.

    - known:       provider name is in the backend registry (else a config typo)
    - needs_key:   this provider authenticates with an API key (claude-max: no)
    - key_present: that key was resolved in THIS process
    - key_var:     the env var name, for the detail message
    - cli_present: for a local (no-host) claude-max checked offline, whether the
                   `claude` binary is on PATH; None when not applicable
    - offline:     config-only mode (no round-trip was attempted)
    - probe_error: the BackendError message if a live round-trip ran and failed,
                   else None
    """
    if not known:
        return BackendCheck(target, DEAD, f"unknown backend '{target.provider}'")
    # Config-only, like the unknown-provider case above and unlike the probe below, so it
    # is decided HERE rather than left to construction. `doctor --offline` never builds a
    # backend, so ClaudeMaxBackend's identical refusal is unreachable on that path -- and
    # an offline run is exactly what someone uses to check a config without touching the
    # network. Reported `ok` before this rule existed, measured.
    #
    # Ahead of the offline branch on purpose: this is true of the CONFIG, so whether a
    # round-trip was attempted has no bearing on it.
    for _field, _value in (("host", target.host), ("claude_path", target.claude_path)):
        if option_like(_value):
            return BackendCheck(
                target, DEAD,
                f"{_field} begins with '-', which ssh and the shelled binary read as an "
                f"OPTION rather than a value (argument injection; e.g. -oProxyCommand=...)")
    if needs_key and not key_present:
        if target.is_primary:
            return BackendCheck(target, DEAD, f"{key_var} unset")
        return BackendCheck(target, DEGRADED, f"{key_var} unset - primary-only")
    if offline:
        if cli_present is False:
            return BackendCheck(
                target, DEAD, f"CLI '{target.claude_path}' not on PATH")
        return BackendCheck(target, OK, "(offline: not round-tripped)")
    if probe_error is not None:
        return BackendCheck(target, DEAD, probe_error)
    return BackendCheck(target, OK, "round-trip ok")


def format_roles(uses: list) -> str:
    """Group a target's uses by role for display, primaries first:
    "primary: triage, cv, track; fallback: cv"."""
    by_role: dict = {}
    for u in uses:
        by_role.setdefault(u.role, []).append(u.subapp)
    parts = []
    for role in ("primary", "fallback"):
        subs = by_role.get(role)
        if subs:
            parts.append(f"{role}: {', '.join(subs)}")
    return "; ".join(parts)


# ── component checks ──────────────────────────────────────────────────────────
# Everything below classifies a piece of the pipeline other than a backend.
# Each function is a pure `facts -> ComponentCheck(es)` mapping, mirroring
# `classify` above; `Sluice.doctor` (core/app.py) gathers the facts.

_MISSING_RENDER_LIBS = (
    "the renderer could not be constructed -- see the message above for the exact "
    "cause. If it names jinja2/weasyprint, `pip install 'job-sluice[render]'`; if the "
    "install already has that extra, WeasyPrint additionally needs its native "
    "libraries (cairo, pango, gdk-pixbuf), which are not a Python dependency (see "
    "README.md's Rendering section for the platform-specific install + the "
    "DYLD_FALLBACK_LIBRARY_PATH note on macOS)."
)


def classify_renderer(error: str | None) -> ComponentCheck:
    """`error` is the RenderError message from constructing `cv.renderer`, or
    None if construction succeeded. Construction is the whole probe -- no PDF
    is written and no LLM is called, so this is cheap and runs under
    `--offline` -- because `renderers/template.py:_make` already raises at
    construction for anything knowable there (a missing extra, a missing
    native library, an unreadable configured template), exactly so a run
    fails before the dossier fetch and the LLM spend rather than after."""
    if error is not None:
        return ComponentCheck("renderer", "cv.renderer", DEAD,
                               f"{error} ({_MISSING_RENDER_LIBS})", blocks=("cv",))
    return ComponentCheck("renderer", "cv.renderer", OK, "constructs ok")


def classify_store(facts: dict | None) -> list:
    """`facts` is the store's own `preflight()` result (see core/protocols.py),
    or None when the configured store does not implement the optional method --
    reported as nothing rather than an error, the same shape `cv/engine.py`
    already gives the renderer seam's optional `precheck`. A store that cannot
    say is not a store that is broken.

    Missing vault or missing baseline CV are DEAD: `cv run` cannot compose
    without a baseline, and every sub-app that touches `self.store()` --
    which, ingest through track, is all five -- treats an unreadable vault the
    same way. A missing Judging Profile is DEGRADED, not dead --
    `core/criteria.py` ships a documented neutral fallback that states only
    "nothing is configured" and never invents an opinion, so triage still
    runs; it just judges nothing preferentially until the profile exists. The
    experience library count is a NOTICE: zero verified entries means every CV
    bullet would fail the fabrication gate's citation check, which is worth
    knowing before a compose, not a defect in the store.

    Candidate Profile (#133/#107) is DEAD, not degraded, on either half-declared
    shape -- a name with no contact, a contact with no name, or neither -- because
    that is exactly the condition `cv/engine.py`'s `skipped-config` refusal already
    gates a real compose on, before any dossier fetch or backend spend. The message
    names only what blocks `cv`: it must not read as a prompt to fill in the other
    31 fields on the note, several of which (ethnicity, disability, religion, sexual
    orientation) are equal-opportunities-monitoring data nobody should feel nudged
    to supply to a tool reporting that something is wrong."""
    if facts is None:
        return []
    out = []
    if not facts.get("vault_exists"):
        # ALL FIVE, not just cv/triage/apply: `Sluice.ingest` (VaultSink),
        # `leads` (expire/dedupe/reconcile) and `track` all call self.store()
        # too -- a missing vault stops the entire pipeline, not a subset of it.
        return [ComponentCheck("store", "vault_dir", DEAD, "vault directory does not exist",
                                blocks=("ingest", "triage", "cv", "apply", "track"))]
    if not facts.get("baseline_exists"):
        out.append(ComponentCheck(
            "store", "baseline_rel", DEAD,
            "baseline CV not found at the configured path -- cv run cannot "
            "compose without it", blocks=("cv",)))
    else:
        out.append(ComponentCheck("store", "baseline_rel", OK, "found"))
    if not facts.get("criteria_present"):
        out.append(ComponentCheck(
            "store", "Judging Profile", DEGRADED,
            "not found -- triage falls back to the shipped neutral default "
            "(no preferential judgement) until you write one"))
    else:
        out.append(ComponentCheck("store", "Judging Profile", OK, "found"))
    verified = facts.get("experience_verified", 0)
    total = facts.get("experience_total", 0)
    out.append(ComponentCheck(
        "store", "Experience Library", NOTICE,
        f"{verified} verified / {total} total entries -- only verified entries "
        f"are citable by the CV fabrication gate"))
    if not (facts.get("candidate_name_present") and facts.get("candidate_contact_present")):
        out.append(ComponentCheck(
            "store", "Candidate Profile", DEAD,
            "no name or no contact details -- cv run refuses to compose "
            "(skipped-config) before any backend call", blocks=("cv",)))
    else:
        out.append(ComponentCheck("store", "Candidate Profile", OK, "found"))
    return out


def classify_track_google(*, available: bool, import_error: str | None,
                           token_present: bool) -> ComponentCheck:
    """`track run` reconciles Gmail + Calendar over `sluice/track/google_client.py`,
    which lazy-imports the google client libraries so the rest of sluice stays
    importable without them (`sluice/` is stdlib-only except for the three
    named, deliberate exceptions -- see CLAUDE.md). DEGRADED only, never DEAD:
    track is one optional sub-app among five, and a job hunt can run entirely
    on the other four."""
    if not available:
        return ComponentCheck(
            "track", "google client libs", DEGRADED,
            f"not importable ({import_error}) -- track run cannot reconcile "
            f"Gmail/Calendar; pip install 'job-sluice[google]'")
    if not token_present:
        return ComponentCheck(
            "track", "google_token.json", DEGRADED,
            "google libs are importable but no token file exists yet -- the "
            "first `track run` will need an interactive OAuth consent")
    return ComponentCheck("track", "google", OK, "libs importable, token present")


def classify_camofox(*, user_env, session_env, resolved_user, probe_capable_sources=()) -> ComponentCheck:
    """Which browser profile an ingest run will drive, and whether the config actually chose it.

    WHY THIS ROW EXISTS. On 2026-08-15 a production runner exported `CAMOFOX_SESSION`, aiming
    at an already-authenticated profile. Profiles are keyed on userId ALONE, so the setting was
    inert and the run used a cookie-less profile; linkedin returned zero rows for eight-plus
    runs and auto-retired. Nothing anywhere reported which profile was in use, which is
    precisely the question `doctor` exists to answer.

    `probe_capable_sources` is what it says: sources that can DETECT a logged-out page, not
    the set that needs a login. Those differ, and conflating them would have this row quietly
    assert that every other source is login-independent. The probe is opt-in, so a source that
    needs auth and ships no probe is simply absent -- which is why the wording promises
    detection rather than coverage.

    Config-only: `Sluice.doctor` never opens a browser, and it does not need to. Every fact
    here is readable from the environment, and the failure being caught was a misconfiguration.

    DEGRADED, never DEAD, for session-without-user: the run still works, on a profile whose
    cookies the operator did not choose. Sources needing no login are unaffected, so it does
    not block a run -- but it is the one shape that is always a mistake.

    `blocks` is set ONLY on the DEGRADED row. `ComponentCheck.blocks` names what a FAILURE
    costs, so putting it on a healthy row prints "blocks: ingest" beside an `ok`.
    """
    profile = camofox_profile_dir(resolved_user)
    detects = ""
    if probe_capable_sources:
        named = ", ".join(sorted(probe_capable_sources))
        detects = (f"; {named} can detect a logged-out page and will report drift=auth "
                   f"rather than a bare zero. Other sources cannot: for them a logged-out "
                   f"profile still looks like an empty result set")
    if session_env and not user_env:
        return ComponentCheck(
            "camofox", "CAMOFOX_USER", DEGRADED,
            f"CAMOFOX_SESSION={session_env!r} is set but CAMOFOX_USER is not. The session key "
            f"does NOT select the cookie profile -- profiles are keyed on CAMOFOX_USER, so "
            f"this run drives {resolved_user!r} ({profile}), not {session_env!r}. Set "
            f"CAMOFOX_USER to the profile you logged in as.{detects}",
            blocks=("ingest",))
    if probe_capable_sources:
        return ComponentCheck(
            "camofox", "CAMOFOX_USER", NOTICE,
            f"profile {resolved_user!r} ({profile}){detects}")
    return ComponentCheck(
        "camofox", "CAMOFOX_USER", OK, f"profile {resolved_user!r} ({profile})")


def list_typed_fields(cfg) -> list:
    """(name, value) for every field of `cfg`'s dataclass whose CURRENT value is
    a list. Value-keyed via isinstance, not the annotation, for the same reason
    `tests/test_sluice_neutral_defaults.py`'s identically-shaped
    `_list_defaulting_fields` is: `list[str]` is a `types.GenericAlias`, not
    `list`, so an annotation-keyed sweep silently misses the first
    `list[str]` field written while looking live. That test enumerates DEFAULT
    values to pin the neutral-defaults invariant (an unconfigured gate ships
    empty); this one enumerates the LOADED config's current values, because
    doctor's job is reporting this install's actual posture, not auditing the
    shipped defaults.

    Deliberately does not attempt an int-typed gate (`contract_floor_gbp_day`,
    `perm_floor_gbp`, `lead_ttl_days`): those are legitimately non-empty in a
    configured install and "0 == abstain" is not a universal reading a generic
    sweep can apply (`lead_ttl_days`'s own bool-subclasses-int hazard is the
    sharpest example) -- CLAUDE.md states this sweep must not be widened to
    ints, and each of those already carries its own named guard elsewhere."""
    return [(f.name, getattr(cfg, f.name)) for f in fields(cfg)
            if isinstance(getattr(cfg, f.name), list)]


def classify_dossier_cache(counts: dict) -> ComponentCheck:
    """The cached-JD length distribution (#169), as one NOTICE row -- deliberately a
    DISTRIBUTION, never a threshold verdict.

    An earlier draft of this made it a threshold NOTICE (a count of dossiers below
    `min_jd_chars`), and three independent reviewers killed it: at the shipped
    `min_jd_chars: 0` the near-empty band is OFF (`DossierCache`'s own docstring), so a
    count against that floor is identically zero -- the one control meant to keep
    #169's accepted residual visible would itself have been INERT at the shipped
    default, exactly the silent-gap shape #169 exists to close. A distribution can
    never be inert: it describes what is actually on disk, at any floor including 0. It
    is also purely descriptive -- unlike `classify_gate`'s preference-gate rows, this
    number changes nothing about which leads get judged -- so 200/800 are a
    PRESENTATION choice (round numbers a human can eyeball at a glance), not a second
    opinion about which jobs are good stacked on top of `min_jd_chars`. Its real payoff
    is that it is exactly the evidence `job-sluice init`'s `min_jd_chars` question
    needs (Task 9): #169 was found only because someone hand-counted a real cache and
    found a material fraction of entries below the 200-character mark -- this row is
    what makes that finding routine instead of a one-off archaeology exercise. (The
    counts from that cache are deliberately not quoted here. They are a measurement of
    one person's private vault, and its size discloses the scale of their job hunt;
    a shipped docstring is a worse place for that than the fixture it would have
    replaced -- see the neutrality rule in CLAUDE.md.)

    `counts["empty"]`, `counts["under_200"]` and `counts["under_800"]` are CUMULATIVE,
    matching #169's own worked example above: `under_200` includes `empty`, and
    `under_800` includes `under_200`. Each bucket therefore answers "how many are AT
    MOST this short", which is independently meaningful without subtracting the others
    first -- `Sluice.doctor` (core/app.py) builds it that way from the real cache.

    `counts["unreadable"]` sits OUTSIDE that chain, not under it -- it is not part of
    the length distribution at all. A dossier file that will not parse (invalid JSON)
    or cannot be read (an interrupted write, a bad disk) has an unknown length, not a
    zero one, so it must never be folded into "empty": an empty JD means the FETCH
    produced nothing (a blocked scraper, a consent wall) -- a scraping problem. An
    unreadable entry means the CACHE FILE itself is broken -- a storage problem. The
    two have different causes and different remedies, and a report that conflates them
    hands a user "50 empty" when their disk is failing, not their scraper. A file that
    parses fine but has no `jd` key (or a malformed one) is a THIRD, distinct shape from
    either: the JSON read succeeded, so it is not "unreadable"; and per
    `DossierCache.jd_arrived`'s own established "cannot say = did not arrive" semantics
    (core/dossier.py), a dossier that cannot answer whether a JD arrived is treated the
    same as one that answers "no" -- so it stays folded into `empty`, not split into a
    fourth bucket. `total` counts every scanned entry, unreadable ones included.

    The buckets do NOT sum to `total`, and an earlier version of this docstring claimed
    they did. `empty`/`under_200`/`under_800` are CUMULATIVE (`empty` ⊆ `under_200` ⊆
    `under_800`), and a healthy dossier of 800 characters or more falls in none of them,
    so the identity is `unreadable + under_800 + (entries at or above 800) == total` --
    which means `unreadable + under_800` is strictly less than `total` on any install with
    a single good JD in it. Reading the printed numbers as a partition would make a
    healthy cache look like it had lost entries.

    Always NOTICE, never DEGRADED/DEAD, for the same reason `classify_gate` is: a
    short-JD-heavy cache is a fact about this install's own scraped data, not evidence
    the PIPELINE itself is broken, so it must never trip `--strict`'s exit code (see
    `DoctorReport.exit_code`'s own reasoning for why NOTICE is excluded by
    construction)."""
    total = counts.get("total", 0)
    if total == 0:
        # The fresh-install shape: nothing has been dossiered yet. Reported as a fact,
        # not folded into the general f-string below, which would otherwise print the
        # slightly odd "0 cached; 0 unreadable, 0 empty, 0 under 200 chars, 0 under 800
        # chars".
        return ComponentCheck("dossier-cache", "cached JDs", NOTICE, "no cached dossiers yet")
    # Labels RENDERED from the same tuple `DossierCache.census` counts with, never
    # hand-written beside it. The boundary was a numeric comparison in one module and an
    # English label in this one, so moving it left the label asserting the old number with
    # the suite green -- the end-to-end fixtures sit well clear of both boundaries, so
    # nothing would have caught the lie. `empty` is spelled out because "under 1 chars" is
    # not what it means.
    lengths = ", ".join(
        f"{counts.get(label, 0)} empty" if label == "empty"
        else f"{counts.get(label, 0)} under {bound} chars"
        for label, bound in _JD_LENGTH_BUCKETS)
    return ComponentCheck(
        "dossier-cache", "cached JDs", NOTICE,
        f"{total} cached; {counts.get('unreadable', 0)} unreadable, {lengths}")


def classify_gate(owner: str, name: str, value: list) -> ComponentCheck:
    """One posture NOTICE per list-typed field `list_typed_fields` swept from a
    loaded config: abstaining (empty) or active (non-empty). Most of these are
    preference gates in the #26/#63 sense (an unconfigured one passes every
    lead through) -- but the sweep is generic over EVERY list-typed field, so
    it also catches `Config.dossier_allow_hosts` (a security allowlist, empty
    meaning "no exceptions granted") and the two noise-word normalization
    lists, neither of which is a preference a lead is judged against. Calling
    the row NOTICE rather than a stronger word is what keeps this harmless
    even where the label overreaches: always NOTICE, never DEGRADED, so an
    abstaining ANYTHING here (the shipped default, and legitimate -- the
    672ad2a incident this whole invariant exists to prevent) never affects the
    exit code or reads as a problem, only as a fact worth knowing before a
    run."""
    subject = f"{owner}.{name}"
    if not value:
        return ComponentCheck("gates", subject, NOTICE, "abstaining (empty)")
    return ComponentCheck("gates", subject, NOTICE, f"active: {len(value)} value(s)")
