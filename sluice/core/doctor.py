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
import hashlib
from dataclasses import dataclass, field, fields

from sluice.core.backends import option_like

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
    """One non-backend fact `sluice doctor` reports on: the renderer, the CV
    identity fields, the store's on-disk artefacts, track's Google adapter, or
    one preference gate's posture.

    `component` groups rows in the printed report ("renderer", "cv-identity",
    "store", "track", "gates"); `subject` names the specific thing
    checked within that group ("cv.renderer", "cv.contact",
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
    """
    specs = [
        # (subapp, role, provider, model, host, claude_path)
        ("triage", "primary", triage_cfg.primary_backend, triage_cfg.claude_max_model,
         triage_cfg.claude_max_host, triage_cfg.claude_max_path),
        ("triage", "fallback", triage_cfg.fallback_backend, triage_cfg.cheap_model,
         *_fallback_host_path(triage_cfg.fallback_backend, triage_cfg.claude_max_host,
                              triage_cfg.claude_max_path)),
        ("cv", "primary", cv_cfg.primary_backend, cv_cfg.compose_model,
         cv_cfg.compose_host, cv_cfg.compose_claude_path),
        ("cv", "fallback", cv_cfg.fallback_backend, cv_cfg.cheap_model,
         *_fallback_host_path(cv_cfg.fallback_backend, cv_cfg.compose_host,
                              cv_cfg.compose_claude_path)),
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


def classify_cv_identity(name: str, contact: str, *, placeholder: str) -> list:
    """The two header-block fields `cv/engine.py`'s STRUCTURAL guards (#99) and
    `skipped-config` refusal already gate a real compose on. Doctor states the
    same two facts BEFORE any spend rather than after: `cv.name` still at the
    shipped placeholder is DEAD (mirrors `skipped-config` -- no STRUCTURAL
    check can tell a placeholder from a genuine name that happens to match
    it, so this is the earliest point the two can be told apart, and only
    because doctor knows the shipped default). `cv.contact` blank is DEGRADED,
    not dead: the packaged template renders it verbatim into the PDF, but a
    user's OWN `cv.template` may hardcode contact details instead, so doctor
    cannot assert this is universally wrong -- only that it usually is."""
    out = []
    if name.strip() == placeholder:
        out.append(ComponentCheck(
            "cv-identity", "cv.name", DEAD,
            f"still the shipped placeholder {placeholder!r} -- a compose would "
            f"refuse before any spend (skipped-config)", blocks=("cv",)))
    else:
        out.append(ComponentCheck("cv-identity", "cv.name", OK, "configured"))
    if not contact.strip():
        out.append(ComponentCheck(
            "cv-identity", "cv.contact", DEGRADED,
            "blank -- the packaged template renders this verbatim; a rendered CV "
            "would carry no way to reach you unless your own cv.template supplies "
            "contact details another way"))
    else:
        out.append(ComponentCheck("cv-identity", "cv.contact", OK, "configured"))
    return out


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
    knowing before a compose, not a defect in the store."""
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


def camofox_profile_dir(user: str) -> str:
    """The on-disk profile directory name for `user`.

    The Camofox persistence plugin stores one profile per userId at a deterministic
    SHA256-hashed subdirectory, of which the directory name is the first 32 hex characters.
    Recomputed from the algorithm rather than carried as a table, so `doctor` cannot print a
    confidently wrong hash that matches nothing on disk."""
    return hashlib.sha256(user.encode()).hexdigest()[:32]


def classify_camofox(*, user_env, session_env, resolved_user, auth_dependent_sources=()) -> ComponentCheck:
    """Which browser profile an ingest run will drive, and whether the config actually chose it.

    WHY THIS ROW EXISTS. On 2026-08-15 a production runner exported
    `CAMOFOX_SESSION=contract-scanner`, aiming at a profile holding 335 cookies. Profiles are
    keyed on userId ALONE, so the setting was inert and the run used the cookie-less `default`
    profile; linkedin returned zero rows for eight-plus runs, and the auto-retire rule then
    removed linkedin, jobserve and indeed. Nothing anywhere reported which profile was in use,
    which is precisely the question `doctor` exists to answer.

    Config-only: `Sluice.doctor` never opens a browser, and it does not need to. Every fact
    here is readable from the environment, and the failure being caught was a misconfiguration.

    DEGRADED, never DEAD, for session-without-user: the run still works, on a profile whose
    cookies the operator did not choose. Sources needing no login are unaffected, so it does
    not block a run -- but it is the one shape that is always a mistake.
    """
    profile = camofox_profile_dir(resolved_user)
    if session_env and not user_env:
        return ComponentCheck(
            "camofox", "CAMOFOX_USER", DEGRADED,
            f"CAMOFOX_SESSION={session_env!r} is set but CAMOFOX_USER is not. The session key "
            f"does NOT select the cookie profile -- profiles are keyed on CAMOFOX_USER, so "
            f"this run drives {resolved_user!r} ({profile}), not {session_env!r}. Set "
            f"CAMOFOX_USER to the profile you logged in as.",
            blocks=("ingest",))
    if auth_dependent_sources:
        named = ", ".join(sorted(auth_dependent_sources))
        verb = "needs" if len(auth_dependent_sources) == 1 else "need"
        return ComponentCheck(
            "camofox", "CAMOFOX_USER", NOTICE,
            f"profile {resolved_user!r} ({profile}); {named} {verb} an authenticated profile "
            f"and will yield zero -- reported as drift=auth, not retired -- if it is logged out",
            blocks=("ingest",))
    return ComponentCheck(
        "camofox", "CAMOFOX_USER", OK,
        f"profile {resolved_user!r} ({profile})", blocks=("ingest",))


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
