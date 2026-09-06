"""sluice doctor: prove the pipeline is actually usable, not just configured.

Pure, zero-I/O core. The impure half -- resolving creds, building a provider,
running a one-token round-trip, constructing (but never writing through) the
store and renderer seams -- lives in `Sluice.doctor` (core/app.py); the
formatting and exit-code plumbing live in `cli.py`. This module owns only the
rules: what is configured (enumeration), and given a set of resolved facts
about one piece of it, is it ok / degraded / dead / awaiting setup / worth a notice
(classification).

Backend classification is ROLE-AWARE, and that is the whole point. The default
install ships a keyless `deepseek` fallback, which `_make_fallback` already
treats as a sanctioned degrade to primary-only -- so a keyless *fallback* is
`degraded` (exit 0), while a keyless *primary* (a run cannot happen) is `setup`
-- unsupplied rather than broken (#243), so it too exits 0, while still naming
the capability it stops.
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
from sluice.core.protocols import (
    ALL_CAPABILITIES, BROKEN, CAPABILITIES, DEGRADED_CAP, EVIDENCE_KINDS,
    NEEDS_SETUP, READY,
)
from sluice.core.stem import stem_all

# Five states, as bare strings so callers (cli formatter, exit_code) and tests
# share one vocabulary without importing an enum. NOTICE is not a severity --
# see DoctorReport.exit_code for why it is excluded from the count rather than
# folded in as the mildest DEGRADED.
OK = "ok"
DEGRADED = "degraded"
DEAD = "dead"
NOTICE = "notice"
# #243. A component the user has simply not SUPPLIED yet, as distinct from one they supplied
# that does not work. Every dead row on a fresh install was the former -- no CV, no verified
# evidence, no Candidate Profile, no `render` extra -- so `doctor` exited 1 on the very command
# `init` tells a new user to run next, and the reassurance that this was expected had to live in
# prose because the exit code said otherwise.
#
# The distinction is "did they give us something broken, or nothing at all": an unset API key is
# SETUP, a key that fails its round-trip is DEAD; a missing baseline is SETUP, an unreadable one
# is DEAD; a renderer whose extra is not installed is SETUP, a renderer NAME that does not exist
# is DEAD. SETUP never reaches the exit code, so `doctor` exits 0 when nothing is broken and 1
# when something is -- which is what a monitor wants to fire on.
#
# It still carries `blocks`, and the row still says which command it stops. Exit 0 means
# "nothing is broken", NOT "everything works"; the verdict line above the table is what says
# what is still needed.
SETUP = "setup"

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
    -- so the printed detail can say what a DEAD/SETUP/DEGRADED row actually costs
    rather than leaving the reader to infer it."""
    component: str
    subject: str
    state: str
    detail: str
    blocks: tuple = ()

    def __post_init__(self):
        """Every name in `blocks` must be a capability the verdict knows (#243).

        `blocks` stopped being decoration when `DoctorReport.verdict()` began reading it
        to decide what the user can still do: a name that matches no capability is
        silently dropped there, so the row keeps printing `blocks: <name>` under
        `--verbose` while the verdict reports that capability -- or every capability --
        as ready. Raising here follows this codebase's fail-loudly-at-construction rule
        and catches it at the row that is wrong, wherever in the tree that row is minted.

        An EMPTY `blocks` stays legal and is not the same mistake: the unreadable
        `stories` corpus carries it deliberately, because nothing reads that corpus, and
        an unread row genuinely stops nothing."""
        unknown = [b for b in self.blocks if b not in ALL_CAPABILITIES]
        if unknown:
            raise ValueError(
                f"ComponentCheck({self.component}/{self.subject}) blocks unknown "
                f"capabilit{'y' if len(unknown) == 1 else 'ies'} {unknown!r}; "
                f"valid names are {list(ALL_CAPABILITIES)}")



# The bare name every sub-app config ships as its `claude` CLI path. One constant rather
# than three literals, so the three loaders cannot drift away from this check --
# `tests/test_doctor_verdict.py` pins it against every `*_claude_path` default there is.
_DEFAULT_CLAUDE_PATH = "claude"


@dataclass
class Verdict:
    """`DoctorReport.verdict()`'s answer: capability LABELS in four buckets, plus the
    rows behind the last three so the caller can print remedies without re-deriving which
    rows mattered."""
    ready: list = field(default_factory=list)
    setup: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    broken: list = field(default_factory=list)
    # capability NAME -> bucket, for a caller that has to look one up (`doctor --require`).
    # The four lists above carry display labels and are what a human reads; this is the
    # machine-readable half, and the only one anything outside this module may key on.
    buckets: dict = field(default_factory=dict)
    setup_rows: list = field(default_factory=list)
    broken_rows: list = field(default_factory=list)
    # Carried so the printer can SHOW the rows `--strict` fails on. Without them a strict
    # run said "Nothing is broken" and exited 1: true on its own terms (nothing is DEAD)
    # and useless, because the rows deciding the exit code were the ones not printed.
    degraded_rows: list = field(default_factory=list)
    # The SUBSET of those that name a capability they stop. Printed in the DEFAULT view,
    # not only under `--strict`, because a row saying `blocks: ingest` is a thing the user
    # must act on whether or not this run's exit code turns on it.
    degraded_blocking_rows: list = field(default_factory=list)


@dataclass
class DoctorReport:
    checks: list  # list[BackendCheck]
    components: list = field(default_factory=list)  # list[ComponentCheck]

    def exit_code(self, *, strict: bool = False) -> int:
        """Non-zero iff a run-blocking backend or component is dead. `--strict`
        additionally fails on any degraded one (the cron mode that enforces a
        believed-in fallback).

        NOTICE and SETUP never contribute, under `--strict` or otherwise --
        that exclusion is BY CONSTRUCTION (the states this loop tests for), not
        a filter applied to a wider set, so neither can be silently dropped by
        deleting one `if`. SETUP is #243: a thing the user has not supplied yet
        is not a fault, and exiting 1 on a fresh install made the first command
        `init` recommends read as a broken install. This is the same posture #26/#63 already state for
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

    def verdict(self):
        """What the user can DO right now, per capability (#243).

        `doctor`'s table answers "is each component healthy". A new user is asking a
        different question -- "what works, and what do I still have to do" -- and a screenful
        of rows across four states is a bad way to answer it. This maps the rows onto the
        CAPABILITIES roster and buckets each one:

          READY     nothing blocks it
          SETUP     everything blocking it is a thing the user has not supplied yet
          DEGRADED  it runs, but a thing the user configured is not doing its job
          BROKEN    at least one thing blocking it is supplied and does not work

        A capability's bucket is the WORST of its blockers, so one genuinely broken row
        moves a capability out of SETUP even when four other rows are merely unsupplied.

        DEGRADED is in that ladder because `blocks` is set on a DEGRADED row too, and it
        means the same thing there: `classify_camofox`'s `CAMOFOX_USER` mismatch carries
        `blocks=("ingest",)` -- the 2026-08-15 incident where a run drove the wrong cookie
        profile and a board returned zero rows for days. Reading `blocks` on only two of
        the five states printed `Ready now: scrape job boards` directly above a `--verbose`
        row saying `blocks: ingest`, and printed that row's remedy nowhere. A DEGRADED row
        with an EMPTY `blocks` still blocks nothing -- which is what keeps the keyless
        fallback, the sanctioned primary-only degrade, out of this entirely.
        Nothing here re-derives a state: it reads the classifiers' verdicts and groups
        them, so the verdict and the table can never disagree about a row.

        Backend rows block only where the target is that sub-app's PRIMARY. A shared
        target that is triage's primary and cv's fallback, with its key unset, stops
        triage and merely degrades cv -- which is exactly what `Sluice.backend()`'s
        `auto` role does at runtime, so reporting it as blocking cv would overstate the
        damage on the commonest multi-sub-app config there is.
        """
        blockers: dict = {name: [] for name, _ in CAPABILITIES}
        for c in self.checks:
            if c.state in (DEAD, SETUP):
                # A `uses` naming a sub-app outside the roster would silently drop its
                # blocker on the floor; `tests/test_doctor_verdict.py` sweeps both
                # producers (this module's `blocks=` tuples and `enumerate_targets`'
                # specs) against CAPABILITIES so that cannot happen unnoticed.
                for u in c.target.uses:
                    if u.role == "primary" and u.subapp in blockers:
                        blockers[u.subapp].append(c)
        for c in self.components:
            # DEGRADED joins the two blocking states here, but only ever contributes
            # through a non-empty `blocks` -- the loop below iterates it, so a DEGRADED row
            # that names nothing adds no blocker and changes no bucket.
            if c.state in (DEAD, SETUP, DEGRADED):
                for subapp in c.blocks:
                    if subapp in blockers:
                        blockers[subapp].append(c)

        ready, setup, degraded, broken = [], [], [], []
        buckets = {}
        for name, label in CAPABILITIES:
            rows = blockers[name]
            states = {c.state for c in rows}
            if not rows:
                bucket, out = READY, ready
            elif DEAD in states:
                bucket, out = BROKEN, broken
            elif DEGRADED in states:
                # Above SETUP deliberately: an unsupplied thing does not run at all and
                # says so, while a misconfigured one runs and quietly does the wrong
                # thing, which is the harder failure to notice.
                bucket, out = DEGRADED_CAP, degraded
            else:
                bucket, out = NEEDS_SETUP, setup
            out.append(label)
            # Keyed by the capability NAME as well, in the same pass. `--require cv` has to
            # look one up, and it must do so by the stable key rather than by the display
            # label -- the labels are prose, free to be reworded, and a CLI contract that
            # broke when someone improved a phrase would be a trap. One dict written beside
            # the four lists, not derived from them afterwards, so the two cannot disagree.
            buckets[name] = bucket
        rows = self.checks + self.components
        degraded_rows = [c for c in rows if c.state == DEGRADED]
        return Verdict(ready=ready, setup=setup, degraded=degraded, broken=broken,
                       buckets=buckets,
                       setup_rows=self.awaiting_setup(),
                       broken_rows=[c for c in rows if c.state == DEAD],
                       degraded_rows=degraded_rows,
                       degraded_blocking_rows=[c for c in degraded_rows
                                               if getattr(c, "blocks", ())])

    def awaiting_setup(self) -> list:
        """Every row the user has not supplied yet, in report order (#243).

        Derived, never hand-listed: the classifiers decide what is SETUP at the point they
        know why a thing is missing, and this reads that back. A roster here would be a second
        opinion about the same fact, and the two would drift."""
        return [c for c in self.checks + self.components if c.state == SETUP]


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
    - cli_present: for a local (no-host) claude-max, checked in BOTH modes (#243), whether the
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
            # SETUP, not DEAD (#243): an unset key is a credential the user has not supplied,
            # not one that fails. A key that IS set and fails its round-trip falls through to
            # `probe_error` below and stays DEAD, which is the distinction that matters to a
            # monitor -- "not configured yet" is not an incident.
            return BackendCheck(target, SETUP, f"{key_var} unset")
        return BackendCheck(target, DEGRADED, f"{key_var} unset - primary-only")
    # BEFORE the `offline` split, deliberately (#243). `Sluice.doctor` now resolves
    # `cli_present` in both modes, and this arm must classify it in both: while it sat
    # inside `if offline:` the two modes disagreed about the same fact -- offline said
    # "CLI not on PATH" and, since #243, SETUP; a live run skipped this, attempted the
    # probe anyway, and reported the failure as `probe_error`, DEAD, exit 1. A fresh
    # install with no `claude` therefore got "Broken: triage leads, tailored CVs, track
    # replies" from plain `job-sluice doctor`, and only `--offline` told the truth.
    if cli_present is False:
        # SETUP only for the SHIPPED default. `claude_max_path`/`compose_claude_path` both
        # default to the bare name `claude`, so "not on PATH" there means the CLI simply is
        # not installed yet -- unsupplied. A user who NAMED a path (a typo, a binary that
        # moved, a homedir that changed) supplied something that does not work, and that
        # must keep exiting 1: otherwise a cron `doctor --strict` goes green on a backend
        # that cannot run.
        if not target.is_primary:
            # A fallback that cannot run is a DEGRADE, exactly like the keyless-fallback
            # arm above -- `auto` still runs primary-only. Without this, two spellings of
            # one fact got opposite `--strict` verdicts: a keyless per-token fallback
            # failed the build while a claude-max fallback whose binary is simply absent
            # passed it, which is the silently-non-functional fallback `--strict` exists
            # to catch.
            return BackendCheck(
                target, DEGRADED,
                f"CLI '{target.claude_path}' not on PATH - primary-only")
        unsupplied = target.claude_path == _DEFAULT_CLAUDE_PATH
        return BackendCheck(
            target, SETUP if unsupplied else DEAD,
            f"CLI '{target.claude_path}' not on PATH")
    if offline:
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

# (#243) There is deliberately NO trailing "...and here is what to do about it" blurb
# appended to a renderer error any more. There used to be, and it restated the remedy the
# error had ALREADY given -- the missing-extra case printed `pip install 'job-sluice[render]'`,
# the INSTALL.md link and the macOS DYLD note twice each, in one 1,207-character line, which is
# the single noisiest row `doctor` prints on a fresh install. Every construction-path raise in
# `renderers/template.py:_make` carries its own remedy (point cv.template somewhere real;
# reinstall; install the extra; fix the template's Jinja2), and `plugins.UnknownAdapter` lists
# the valid names, so the generic restatement added no fact -- only length. Keep it that way:
# a remedy belongs at the raise site, which knows which case it is, not here, which does not.
# `tests/test_doctor_verdict.py` pins that every renderer row is self-contained.


def classify_renderer(error: str | None, *, missing_dependency: bool = False) -> ComponentCheck:
    """`error` is the RenderError message from constructing `cv.renderer`, or
    None if construction succeeded. Construction is the whole probe -- no PDF
    is written and no LLM is called, so this is cheap and runs under
    `--offline` -- because `renderers/template.py:_make` already raises at
    construction for anything knowable there (a missing extra, a missing
    native library, an unreadable configured template), exactly so a run
    fails before the dossier fetch and the LLM spend rather than after."""
    if error is not None:
        # `missing_dependency` is decided by the CALLER from the exception TYPE, never by
        # matching this message (#243). `core/app.py` asks
        # `isinstance(e, RenderDependencyError)` -- the seam member a renderer raises to say
        # "something I need is not installed" -- and nothing else. A `plugins.UnknownAdapter`
        # naming a renderer that does not exist, and a plain `RenderError` from a missing
        # template or a template syntax error, are things the user supplied that do not
        # work, and stay DEAD. Deliberately NOT `__cause__`-sniffing, which is what this
        # comment used to describe: `core/protocols.py` has the three ways that was wrong.
        # String-matching this text would tie classification to wording free to change.
        return ComponentCheck("renderer", "cv.renderer",
                              SETUP if missing_dependency else DEAD,
                              error, blocks=("cv",))
    return ComponentCheck("renderer", "cv.renderer", OK, "constructs ok")


def classify_store(facts: dict | None) -> list:
    """`facts` is the store's own `preflight()` result (see core/protocols.py),
    or None when the configured store does not implement the optional method --
    reported as nothing rather than an error, the same shape `cv/engine.py`
    already gives the renderer seam's optional `precheck`. A store that cannot
    say is not a store that is broken.

    Missing vault or missing baseline CV BLOCK (#243: SETUP when the user has not
    supplied them, DEAD when a named vault is gone): `cv run` cannot compose
    without a baseline, and every sub-app that touches `self.store()` --
    which, ingest through track, is all five -- treats an unreadable vault the
    same way. A missing Judging Profile is DEGRADED, not dead --
    `core/criteria.py` ships a documented neutral fallback that states only
    "nothing is configured" and never invents an opinion, so triage still
    runs; it just judges nothing preferentially until the profile exists. Each of
    the three evidence corpora (#164: Experience Library, Skills Inventory, STAR
    Stories) gets its own row -- NOTICE, with one exception. For a corpus the gate
    actually READS (`EvidenceKind.cited_by_gate` -- `experience` alone today), zero
    verified entries BLOCKS -- SETUP, `blocks=("cv",)` (#242, restated for #243): `cv run` refuses such a
    vault outright, once for the run and before any fetch or backend call, so a
    NOTICE row would call the install fine about the exact thing that stops the next
    command. That reverses this docstring's earlier reading -- "worth knowing before
    a compose, not a defect in the store" -- which was true only while the compose
    still attempted and failed later. The other two say so rather than claiming a citability they do not have,
    and since #165 they differ from each other: `skills` is READ by the composer as
    framing (`read_by_composer`) while remaining uncitable, so its row says that rather
    than either "citable" or "nothing reads this". In every case a non-zero
    PENDING count is the same tier again, because propose-only writes leave
    entries sitting in `_inbox/`, doing nothing, until a human runs `job-sluice
    <kind> verify`; the message names that exact command; a count nobody can
    act on is noise, not a notice. A kind `preflight` reports a `<kind>_error`
    for instead of a count triple takes its own DEAD row carrying that text --
    per-kind, so one unreadable corpus costs exactly its own row and every other
    store row survives (round-2 review, H2).

    Candidate Profile (#133/#107) BLOCKS cv, not merely degrades it, on either half-declared
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
        # SETUP only when the path is the shipped default -- i.e. nobody has configured a
        # vault yet, a genuine pre-`init` install. When the user NAMED one (config key or
        # `$VAULT_DIR`) and it is not there, the vault has MOVED or been deleted: an
        # unmounted drive, a renamed Obsidian folder, a Syncthing path change, a typo.
        # That stops every sub-app, and reporting it as an unfinished setup step made
        # `doctor` exit 0 saying "Nothing is broken." on an install where nothing works --
        # measured. Same explicit-vs-default distinction `core/paths.py` draws, for the
        # same reason: naming a path is a claim that it is the right one.
        #
        # An absent fact keeps the pre-#243 DEAD rather than defaulting to the quieter
        # SETUP: a store that does not report the distinction has not earned the benefit
        # of it.
        explicit = facts.get("vault_dir_is_default") is not True
        return [ComponentCheck("store", "vault_dir", DEAD if explicit else SETUP,
                               "vault directory does not exist -- "
                               "`job-sluice init --vault PATH` creates one",
                               blocks=ALL_CAPABILITIES)]
    if not facts.get("baseline_exists"):
        out.append(ComponentCheck(
            "store", "baseline_rel",
            # Same explicit-vs-default rule as `vault_dir` above, and it belongs here for
            # the same reason: `baseline_rel` is a root config key, so a user who set it
            # told sluice where their CV IS. If it is not there, they renamed or moved it --
            # every `cv run` now refuses before any spend, and `doctor`, the command they
            # run to find out why, would otherwise say "Nothing is broken." and exit 0.
            # At the shipped default nobody has said anything, so it is unsupplied.
            SETUP if facts.get("baseline_rel_is_default") is True else DEAD,
            "baseline CV not found, or empty, at the configured path -- cv run cannot "
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
    # Iterates EVIDENCE_KINDS rather than a hand-listed (kind, label) tuple, so a
    # fourth kind registered there needs no edit here. The label is the store's
    # own relpath basename ("Job Applications/Skills Inventory" -> "Skills
    # Inventory") rather than a second, hand-maintained name -- EvidenceKind
    # carries no display label of its own, and inventing a second name for the
    # same directory is exactly the two-sources-for-one-fact shape this file's
    # own docstring (and CLAUDE.md) calls out elsewhere.
    for kind, spec in EVIDENCE_KINDS.items():
        label = spec.relpath.rsplit("/", 1)[-1]
        error = facts.get(f"{kind}_error")
        if error:
            # DEAD, not NOTICE: the counts row below is informational, but this one says
            # the store could not read the corpus AT ALL, and the three commands that
            # manage it (`job-sluice <kind> add|list|verify`) fail the same way until the
            # user acts. NOTICE never reaches the exit code (see DoctorReport.exit_code),
            # which would make a genuinely broken directory exit 0 -- the quiet direction
            # this codebase refuses to fail in.
            #
            # `blocks` is set only for a corpus the gate actually READS. Measured with
            # `Job Applications/Experience Library` symlinked out of the vault:
            # `read_evidence("experience")` RAISES rather than returning [], so `cv/engine.py`'s
            # `run_one` never builds a bundle and `run_batch`'s per-lead catch-all records
            # `error` for every lead -- the same "cv run cannot compose" cost the
            # `baseline_rel` row above already names. Keyed on `cited_by_gate`, NOT on
            # `read_by_composer`: since #165 an unreadable `skills` corpus does not block
            # `cv` at all -- `cv/engine.py` catches it, warns, and composes without the
            # framing -- so naming a sub-app there would over-claim in the other
            # direction.
            out.append(ComponentCheck(
                "store", label, DEAD, f"cannot be read -- {error}",
                blocks=("cv",) if spec.cited_by_gate else ()))
            continue
        # `_MISSING`, not a `0` default: since #242 a zero here BLOCKS cv -- it moves the
        # capability out of `Ready now` (#243 made the state SETUP, so it no longer moves
        # the exit code, but the row still stops the next command) -- so defaulting an
        # ABSENT fact to zero would manufacture the very "read failure reported as an
        # empty count" that `Vault.preflight` forbids. Not
        # reachable through `Vault` (it always supplies the triple, or the `<kind>_error` arm
        # above fires instead), but a second store need only omit the key to trip it.
        verified = facts.get(f"{kind}_verified")
        fact_missing = verified is None
        verified = verified or 0
        total = facts.get(f"{kind}_total", 0)
        pending = facts.get(f"{kind}_pending", 0)
        # Keyed on the registry's own `cited_by_gate`, not printed for every kind:
        # `cv/engine.py` reads `experience` alone, so telling a user that verifying a
        # skill made it "citable by the CV fabrication gate" was simply false, and
        # false in the reassuring direction -- they read it as "my skills are feeding
        # my CVs" and stop looking (#164 review, M2). The verify row below still
        # applies to every kind: `verify` is the trust root regardless of who reads
        # the result, and an entry stuck in `_inbox/` is inert either way.
        if spec.cited_by_gate:
            detail = (f"{verified} verified / {total} total entries -- only verified "
                      f"entries are citable by the CV fabrication gate")
        elif spec.read_by_composer:
            # True for `skills` since #165: the composer is SHOWN them as framing, the gate
            # licenses no figure from them, and the #60 advisory audit is not shown them at
            # all (cv/bundle.py's two renderers). "citable" here would be the #164 M2
            # over-claim; "nothing reads this corpus" is now simply false.
            detail = (f"{verified} verified / {total} total entries -- shown to the CV "
                      f"composer as framing; not a citable source for the gate")
        else:
            detail = (f"{verified} verified / {total} total entries -- reviewed, but "
                      f"nothing reads this corpus yet")
        if pending:
            # The failure mode propose-only writes introduce: entries captured,
            # sitting in `_inbox/`, doing nothing, with no other signal anywhere
            # that a human needs to review them. Naming the exact command is the
            # whole value of this row -- a count nobody can act on is just noise.
            detail += (f"; {pending} proposed and awaiting review "
                       f"(job-sluice {kind} verify)")
        # BLOCKING (SETUP), not NOTICE, when a CITABLE corpus has nothing verified in it (#242): `cv run`
        # now refuses such a vault outright, before any spend, so a NOTICE row here would say
        # the install is fine about the very thing that makes the next command exit 2. The
        # Candidate Profile row below is the precedent -- the same shape, blocking, naming the
        # refusal it predicts -- and predicting which commands are blocked is what this report
        # is for. Only for `cited_by_gate`: an empty `skills` or `stories` corpus blocks
        # nothing, so those stay NOTICE and say so in their own wording above.
        if spec.cited_by_gate and not verified and not fact_missing:
            out.append(ComponentCheck(
                "store", label, SETUP,
                detail + " -- cv run refuses to compose without at least one",
                blocks=("cv",)))
        else:
            out.append(ComponentCheck("store", label, NOTICE, detail))
    if not (facts.get("candidate_name_present") and facts.get("candidate_contact_present")):
        out.append(ComponentCheck(
            "store", "Candidate Profile", SETUP,
            "no name or no contact details -- cv run refuses to compose "
            "(skipped-config) before any backend call", blocks=("cv",)))
    else:
        out.append(ComponentCheck("store", "Candidate Profile", OK, "found"))
    return out


def classify_track_google(*, available: bool, import_error: str | None,
                           token_present: bool, token_path: str = "") -> ComponentCheck:
    """`track run` reconciles Gmail + Calendar over `sluice/track/google_client.py`,
    which lazy-imports the google client libraries so the rest of sluice stays
    importable without them (`sluice/` is stdlib-only except for the three
    named, deliberate exceptions -- see CLAUDE.md). DEGRADED only, never DEAD:
    track is one optional sub-app among five, and a job hunt can run entirely
    on the other four.

    SETUP since #243, and `blocks=("track",)` on both arms. An uninstalled `google` extra is
    the same fact as an uninstalled `render` extra, and a token the user has not minted is
    the same fact as an unset API key -- both of which this file already calls SETUP, so
    calling these DEGRADED was two spellings of one idea. `blocks` is the load-bearing half:
    without it `verdict()` sees no blocker and the default view printed
    `Ready now: track replies` on an install where `track run` cannot reach Gmail at all,
    with the remedy on neither line -- the identical defect `classify_camofox` carries a
    `blocks` to avoid, arrived at from the other direction (an empty `blocks` rather than an
    unread state).

    The state change has one consequence worth stating rather than discovering: `--strict`
    used to fail on both of these and no longer does. That is the intended reading -- an
    optional sub-app the user has not set up is not a fault -- but it is a change to what a
    cron alert fires on, not a tidy-up."""
    if not available:
        return ComponentCheck(
            "track", "google client libs", SETUP,
            f"not importable ({import_error}) -- track run cannot reconcile "
            f"Gmail/Calendar; pip install 'job-sluice[google]'", blocks=("track",))
    if not token_present:
        # The RESOLVED path, not the config key's name. `track.token_path` resolves through a
        # config key then an XDG root, so telling someone their token is missing without saying
        # from where leaves them to guess which of those applied -- and this row's whole job is
        # to be actionable. Defaulted rather than required so the ~existing direct callers in the
        # suite keep working; the caller that matters passes it.
        where = f" at {token_path}" if token_path else " at track.token_path"
        return ComponentCheck(
            "track", "google_token.json", SETUP,
            f"google libs are importable but no token file exists yet{where} -- "
            "`track run` cannot reach Gmail/Calendar until one does. sluice does not run "
            "the OAuth consent flow itself; see https://github.com/MrReasonable/sluice/"
            "blob/main/docs/INSTALL.md#google-access-for-track for how to produce the token",
            blocks=("track",))
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
    return [(f.name, getattr(cfg, f.name), f.metadata.get("gate_role"))
            for f in fields(cfg)
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

    Always NOTICE, never DEGRADED/DEAD, for the same reason `classify_gate`'s DECLARED-role
    rows are (its undeclared-role row is the one exception, and describes a wrong-shaped
    value rather than a gate posture): a
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


# Matches `cv/engine.py:_jd_keywords`' own `[a-z]{4,}`, so the two places that reduce prose
# to comparable keywords agree on what is too short to carry meaning. A LENGTH floor, not a
# stopword list -- see classify_negatives_vs_skills for the measured case it closes.
_MIN_TERM_LEN = 4


def classify_negatives_vs_skills(negatives: list, skills: list) -> list:
    """One NOTICE per configured `cv.negatives` entry naming a skill the verified Skills
    Inventory actually holds (#165).

    `cv.negatives` is prose asserting which technologies the candidate does and does not
    work in, maintained by hand and separately from the inventory that already answers
    that. The bundle's derived cross-reference (`cv/bundle.py:_DERIVED_NEGATIVE_PROMPT`)
    cannot stop the two disagreeing -- it names nothing, so it adds a third voice rather
    than replacing the stale one. This is what makes the disagreement visible.

    Matches on `best_for` ONLY -- the floor key `EVIDENCE_KINDS["skills"]` maps onto
    `Domain`, the kind's classification axis. The entry TITLE is excluded: it is a name the
    user chose, so matching its stems makes an ordinary word in it ('skill', 'example')
    fire a NOTICE about nothing.

    Both sides are then floored at `_MIN_TERM_LEN` characters. A `Domain` reading "Data and
    analytics for the platform" otherwise contributes the stem `the`, and every negative
    containing the word "the" reports a contradiction -- measured, and NOT covered by the
    asymmetry this docstring used to claim was accepted. The floor is 4 to match
    `cv/engine.py:_jd_keywords`' own `[a-z]{4,}` extraction, so the two places in this
    codebase that turn prose into comparable keywords agree on what is too short to mean
    anything. It is a LENGTH rule, not a vocabulary: no stopword list ships, which is the
    thing this repo declines to do.

    Above the floor the NEGATIVE side stays unfiltered, and that asymmetry IS accepted: a
    loose sentence that happens to contain the inventory's own domain word is still a
    contradiction worth looking at.

    The row NAMES THE INDEX and the overlap SIZE, never the configured text or the matched
    terms. A DoctorReport is returned whole to MCP clients (`sluice/mcpserver.py`), and
    `classify_gate` below reports this same key as a COUNT for exactly that reason;
    echoing the user's own preference prose into a diagnostic would make it a disclosure
    surface.

    NOTICE, never DEGRADED, so it cannot affect the exit code: `--strict` in a cron job
    failing because a negative overlaps an inventory is the 672ad2a class aimed at the
    tool's own exit status. Same posture `classify_gate` takes for a declared role; its
    undeclared-role row is DEGRADED, but that is a wrong-shaped VALUE, not a gate posture.

    Abstains on either empty input, and on an inventory whose entries declare no domain at
    all -- an install with nothing to contradict must report nothing.
    """
    if not negatives or not skills:
        return []
    terms = {t for e in skills for t in stem_all(e.get("best_for", ""))
             if len(t) >= _MIN_TERM_LEN}
    if not terms:
        return []
    out = []
    for i, neg in enumerate(negatives):
        overlap = {t for t in stem_all(neg) if len(t) >= _MIN_TERM_LEN} & terms
        if overlap:
            out.append(ComponentCheck(
                "gates", f"cv.negatives[{i}]", NOTICE,
                f"contradicts the verified Skills Inventory on {len(overlap)} term(s) -- "
                f"the composer is told both. Compare this line against `job-sluice skills "
                f"list`; remove the line, or remove the skill."))
    return out


# The Experience Library frontmatter field this module reads, named once. `_declared_skills`
# and BOTH halves of the row `classify_skills_request` prints -- the subject and the detail
# -- derive from it, so a renamed field cannot leave either labelled after the old one. The
# detail is the half that matters there: it is the one an operator reads as an instruction,
# and it spelled `Skills:` as a bare literal until review caught the constant reaching only
# the subject.
#
# The constant itself carries NO trailing colon, which is why the detail adds one and the
# subject does not. The subject is asserted verbatim in tests/, and `Skills:` followed by
# anything in a test file is read as a declared skill VALUE by
# `test_fixture_name_neutrality.py`'s collector -- measured: spelling the subject
# `Experience Library (Skills:)` puts `)` on the reviewed-name roster and reddens that
# guard. The detail keeps its colon because that is what an operator greps their notes for
# and no test asserts that substring; one that did would trip the same collector, which
# fails SAFE (the guard reddens and asks a human) but is worth knowing before writing it.
_SKILLS_FIELD = "Skills"


def _declared_skills(entry: dict):
    """The `Skills:` names ONE experience entry declares: a set, or None when the value is
    one `cv/bundle.py`'s `_skill_items` could not read at all.

    The single reader of that field in this module, shared by `classify_skills_request`
    and `classify_skills_reconciliation` below. The two rows ask different questions of
    the same field -- "does this entry annotate anything at all" and "what does it claim"
    -- and a second reader written for one of them would let the two disagree about the
    same note. Same discipline as `core/vault.py`'s `_fold_note_name`: a reduction every
    caller has to agree on gets one definition, not one per call site.

    THREE outcomes, not two, because the gate has three. `_skill_items` returns no items
    for a blank value, returns items for an annotated one, and RAISES for anything it
    cannot read -- `.split` on a non-str. An empty set here means the first; None means
    the third. Collapsing them (an earlier cut returned an empty set for both) made this
    function report "no annotation" for a value that in fact stops `cv run` dead, and
    `classify_skills_request` then fired a reassuring row about a corpus that composes
    nothing. That is the same shape its non-blank suppression already exists to prevent,
    on the one arm the suppression could not see; the two callers need the distinction, so
    the reader draws it rather than each caller re-deriving it. Same
    cannot-say-is-not-zero rule `classify_store` applies to an absent preflight count.

    `.get(...)` returns None when the key is ABSENT, which is a genuine blank -- `_skill_items`
    defaults it to `""` and reads no items -- so the missing key is normalised to `""` HERE
    rather than falling into the non-str arm. What remains non-str is a value the Store
    actually supplied: an explicit `None`, an int, a list. core/protocols.py's Store contract
    forbids none of them, even though the real Vault never produces one (`_evidence_entries`
    materialises every declared field via `fm.get(k, "")`).

    Nothing here raises, and nothing is COERCED. doctor never refuses (this module's house
    rule -- see `DoctorReport.exit_code` and CLAUDE.md), and stringifying a list would
    render Python's own repr, whose brackets and quotes the comma-split would then read as
    further "skill names" -- carrying a Store's returned content into a report these rows
    promise holds only COUNTS.

    Split HERE rather than through `_skill_items` itself -- `sluice/core/` must not import a
    sub-app (CLAUDE.md's layering rule). Given the None arm the two readings now partition
    a value the same way: blank under one is blank under the other, and every value
    `_skill_items` raises on is one this returns None for. They still differ in DEGREE on a
    non-blank value -- `_skill_items` also refuses an item carrying no name (`...`, `-`)
    where this returns it as an item -- but both are then non-blank, which is all either
    caller asks. `classify_skills_request` says why that residual difference cannot reach
    a row.
    """
    raw = (entry.get("fields") or {}).get(_SKILLS_FIELD, "")
    if not isinstance(raw, str):
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


def classify_skills_request(experience_entries: list) -> list:
    """One NOTICE naming the precondition a composed SKILLS section actually has, emitted
    only while NO verified experience entry annotates a `Skills:` field (#259).

    `cv/engine.py` asks for the section with
    `skills_requested=any(es.skills for es in sources.entries.values())`, computed over
    `bundle_sources(build_bundle(entries, ...))` where `entries` is
    `read_evidence("experience", verified_only=True)` -- the FULL verified set on every
    lead, since `cv/bundle.py`'s `rank` orders and never excludes. One corpus-wide fact
    therefore decides it for every lead at once: with no `Skills:` value anywhere, no CV
    ever gets a SKILLS section.

    The Skills Inventory appears NOWHERE in that chain. It is framing the composer is
    shown, licensing nothing (`EvidenceKind.read_by_composer`, `cited_by_gate=False`), and
    `classify_store`'s row for it says exactly that -- while still being outweighed by the
    `<verified> / <total>` ratio in front of the sentence and by sitting beside the
    Experience Library row, where that same ratio genuinely IS what gates citability.
    Measured on a vault whose every verified experience entry carried a blank `Skills:`:
    that inventory row was the only thing doctor said on the subject, verifying all of it
    changed nothing, and the real precondition was reported by nothing at all. This row is
    that missing fact. It takes the `store` component so it is read beside the row it
    corrects; `classify_skills_reconciliation` below uses `gates` because it
    cross-references TWO corpora, whereas this is one corpus's own content, like
    `classify_store`'s per-kind counts.

    SUPPRESSED by any entry the gate would not read as blank, and that is what keeps the
    claim exact rather than merely usually true. TWO shapes suppress, for one reason.
    An entry that genuinely annotates the field is the obvious one. The other is an entry
    `_skill_items` REFUSES -- a non-blank but nameless item (`...`, `-`), or a value it
    cannot split at all (`_declared_skills` returns None) -- which raises out of
    `build_bundle` before any compose and fails every lead in the batch. Firing there
    would announce that no CV gets a SKILLS section about a corpus that in fact composes
    no CV at all: true by accident, reassuring, and pointed at a remedy already taken.
    So the row is emitted only where the two readings of the field agree that nothing is
    annotated, which no difference in how strictly they read a NON-blank value can reach.
    Zero is also the only count an operator can act on; a non-zero one asks for nothing.

    It does NOT report the refused corpus itself. That is `classify_store`'s job -- it
    already emits a per-kind DEAD row from the `<kind>_error` a store reports for a corpus
    it cannot read -- and inventing a second, differently-worded row for it here would put
    one fact in two places. This row's whole contribution is the count, so where the count
    would mislead it says nothing.

    ABSTAINS on an empty corpus rather than reporting `0 of 0`. `classify_store` already
    emits a SETUP row there that BLOCKS `cv` (#242: `cv run` refuses a vault with nothing
    verified), so the operator's next step is to verify an entry, not to annotate one that
    does not exist yet -- and a row naming the further step would compete with the row
    naming the blocking one.

    NOTICE, never DEGRADED or SETUP, and no `blocks`: a vault with no `Skills:` annotation
    anywhere is the shape EVERY vault had the day #168 landed (`_skill_items`' own "Blank
    is absent") and is fully supported -- `cv run` composes a CV without the section, so
    nothing is stopped. `--strict` in a cron job must not fail on it, the same posture
    `classify_skills_reconciliation` and `classify_negatives_vs_skills` take and for the
    same reason (see `DoctorReport.exit_code`).

    Reports a COUNT, never a skill name. There is none to report at zero, and the rule
    holds regardless: a `DoctorReport` is returned whole to MCP clients
    (core/protocols.py's Store contract).
    """
    if not experience_entries:
        return []
    for e in experience_entries:
        declared = _declared_skills(e)
        # None (the gate cannot read this value) suppresses exactly as a real annotation
        # does, and for the same reason: both mean this row's claim would be false.
        if declared is None or declared:
            return []
    # Both labels derived from the registry `classify_store` reads its own subjects from,
    # never hand-typed: the detail below distinguishes two corpora by name, and a second
    # spelling of either is the two-sources-for-one-fact shape this file avoids elsewhere.
    experience_label = EVIDENCE_KINDS["experience"].relpath.rsplit("/", 1)[-1]
    skills_label = EVIDENCE_KINDS["skills"].relpath.rsplit("/", 1)[-1]
    return [ComponentCheck(
        "store", f"{experience_label} ({_SKILLS_FIELD})", NOTICE,
        f"0 of {len(experience_entries)} verified entries carry a {_SKILLS_FIELD}: field "
        f"-- until one does, no CV gets a SKILLS section. The field goes on the "
        f"{experience_label} entry note itself, not on a {skills_label} entry "
        f"(job-sluice experience list)")]


def classify_skills_reconciliation(experience_entries: list, skills_entries: list) -> list:
    """Up to two NOTICE rows cross-referencing verified experience entries' `Skills:`
    claims against the verified Skills Inventory (#168 Task 10) -- modelled on
    `classify_negatives_vs_skills` immediately above: two hand-maintained corpora
    nothing else here keeps in agreement, made VISIBLE rather than left to silently
    drift.

    Neither direction is an error. `Skills:` licenses a CV bullet's own numbers
    RELATIONALLY (cv/bundle.py's `_skill_items`/`_entry_skills_line`) with no
    requirement that the name it claims also exist as its own Skills Inventory entry --
    a user may simply type a skill name straight into `Skills:` and never curate an
    inventory entry for it at all. And a Skills Inventory entry is read by the composer
    purely as FRAMING (`EvidenceKind.read_by_composer`, cited_by_gate=False) with no
    requirement that any experience entry cite it back -- the spec's own words for this
    row are "framing-only, licensing nothing". So both directions are NOTICE, never
    DEGRADED, the same posture `classify_negatives_vs_skills` and `classify_gate`'s
    declared-role rows take:
    `--strict` in a cron job must not fail an install that simply has not (yet, or
    ever) linked the two corpora together (see `DoctorReport.exit_code`'s own
    reasoning for why NOTICE is excluded from the count by construction).

    IDENTITY. An experience entry's `Skills:` value is free TEXT a user typed into an
    ordinary frontmatter field, never reduced. A Skills Inventory entry's `title` is
    the STORED FILENAME, which for every entry `propose_evidence` created is
    `evidence_slug(name)` -- lowercase, dash-separated (core/vault.py). Comparing the
    two verbatim would therefore almost never match a real pair ("Example Widget3" vs
    "example-widget3"). `_keys` reduces a typed name through the SAME `evidence_slug`
    call `Sluice.verify_evidence_interactive`'s own `--id` lookup already imports and
    uses (`entry["title"] == only or entry["title"] == reduced`, core/app.py), applying
    the identical verbatim-or-reduced comparison shape around it. The REDUCTION cannot
    drift between the two call sites since it is one shared function; the comparison
    itself is written independently at each -- currently identical, so the two checks
    agree today, but that agreement is not structurally enforced the way sharing the
    reduction is. A name that fails to reduce (`evidence_slug` raises on an
    all-punctuation name) falls back to the verbatim form alone, mirroring that same
    call site.

    PARSING. `Skills:` is comma-separated free text, split HERE rather than through
    `cv/bundle.py:_skill_items` -- `sluice/core/` must not import a sub-app (CLAUDE.md's
    layering rule). Verified narrowly, not as a blanket claim about `sluice/core/` as a
    whole: `core/doctor.py` ITSELF imports nothing from `sluice.cv`, which is the
    property this function's own layering actually depends on. `core/app.py` (a
    DIFFERENT `core/` module, the documented composition root every sub-app is wired
    through) does import from `sluice.cv`, lazily, inside individual method bodies --
    `sluice.cv.config` inside BOTH `Sluice.compose_cv` and `Sluice.doctor` itself (the
    method that builds the `DoctorReport` this function's rows end up in), and
    `sluice.cv.engine` inside `compose_cv` alone. Those imports are deliberate (the
    composition root doing its job) and unrelated to this function's own layering
    claim, which is only ever about `core/doctor.py`. This reconciliation is also
    informational rather than gate-enforcing, so it must never RAISE the way that
    function's own per-token validation does on a malformed entry -- doctor never
    refuses (this module's own house rule, stated at `DoctorReport.exit_code` and in
    CLAUDE.md). A `Skills` VALUE that is present but not a `str` (an `int`, a `list`)
    is handled by ABSTAINING -- treated as no claim at all -- rather than by
    coercing it with `str()`: stringifying a list renders Python's own repr
    (brackets, quotes, comma-separated elements), and this function's own comma-split
    would then read those as further "skill names" and could carry a non-string
    Store's own returned content into a comparison this function's docstring
    elsewhere promises reports only a COUNT. Abstaining keeps that promise; coercing
    would not.

    REPORTS A COUNT, never the skill's own name: a `DoctorReport` reaches MCP clients
    whole (core/protocols.py's Store contract), and "no doctor row carries user-authored
    text today" is this codebase's own standing rule (see the spec's section 7) -- a
    skill name is exactly that, this person's own claimed expertise.

    ABSTAINS unless BOTH corpora contribute something -- at least one `Skills:` claim
    and at least one inventory entry -- exactly as `classify_negatives_vs_skills` above
    abstains on either empty input and again on a vocabulary that reduces to nothing.
    A reconciliation is a statement about two corpora DRIFTING; with one side empty
    there is no drift to report, only the other side counted at 100%. Both such installs
    are fully supported and neither is a mistake: `Skills:` licenses a bullet's numbers
    RELATIONALLY with no requirement that an inventory entry exist, and an inventory
    entry is framing that requires no experience entry to cite it back. So a row fired
    against an empty other side would be permanent, uninformative, and pointed at a
    remedy the user has deliberately not taken -- the empty-means-abstain posture
    CLAUDE.md states for every preference gate, applied here to a NOTICE row instead.
    (Before this, an empty inventory reported EVERY declared `Skills:` name as unmatched
    while the mirror row silently abstained, and an install with no `Skills:` annotation
    anywhere -- the shape every pre-#168 vault has -- reported every inventory entry as
    unclaimed. The asymmetry was in the counts, not in the rule.)

    Past that guard both counts are computed independently and each row is still
    suppressed at zero, so a partial overlap reports only the direction that actually
    disagrees.
    """
    from sluice.core.vault import evidence_slug

    def _keys(name: str) -> set:
        try:
            return {name, evidence_slug(name)}
        except ValueError:
            return {name}

    # `_declared_skills` above, not a second split written here: `classify_skills_request`
    # decides whether ANY entry annotates the field and this decides WHAT each one claims,
    # and the two questions must be answered off one reading of the note. Its docstring
    # carries the absent-key / None-value / non-str handling that used to live here.
    claimed = set()
    for e in experience_entries:
        # `or set()`: a value `_declared_skills` could not read (None) contributes no NAME
        # here, which is what this row has always done with one -- unlike
        # `classify_skills_request`, which must suppress on it. One reader, two mappings,
        # each stated where it is made.
        claimed |= _declared_skills(e) or set()

    titles = {e.get("title", "") for e in skills_entries}
    # The abstain, on the DERIVED vocabularies rather than the raw arguments -- the same
    # two-stage shape `classify_negatives_vs_skills` uses, and for the same reason: an
    # experience corpus none of whose entries carries a usable `Skills:` value is the
    # identical "nothing to reconcile" state as no experience corpus at all, and a raw
    # `if not experience_entries` guard would miss it. See the docstring for why one
    # empty side is an abstain rather than a 100% row.
    if not claimed or not titles:
        return []

    claimed_keys = set().union(*(_keys(n) for n in claimed))

    unclaimed = sum(1 for title in titles if title not in claimed_keys)
    unmatched = sum(1 for name in claimed if not _keys(name) & titles)

    # Derived from the SAME registry `classify_store` above reads its own per-kind
    # subjects from -- but suffixed, DELIBERATELY not reused bare: `classify_store`
    # already emits a "store"-component row at the bare "Skills Inventory"/
    # "Experience Library" subject for each corpus's own total/verified/pending
    # count, and a reader (or a test keying on `subject` alone rather than the
    # `(component, subject)` pair together) could not tell that row apart from this
    # one. Measured: a mutation deleting this function's call site in Sluice.doctor
    # left `test_the_skills_reconciliation_runs_through_the_real_wiring` GREEN when
    # the two subjects were bare, because `classify_store`'s own rows alone already
    # satisfied the subject-set assertion.
    experience_label = EVIDENCE_KINDS["experience"].relpath.rsplit("/", 1)[-1]
    skills_label = EVIDENCE_KINDS["skills"].relpath.rsplit("/", 1)[-1]

    out = []
    if unclaimed:
        out.append(ComponentCheck(
            "gates", f"{skills_label} (unclaimed)", NOTICE,
            f"{unclaimed} inventory skill(s) evidenced by no entry -- "
            f"job-sluice experience list"))
    if unmatched:
        out.append(ComponentCheck(
            "gates", f"{experience_label} (unmatched)", NOTICE,
            f"{unmatched} entry Skills: name(s) absent from the inventory -- "
            f"job-sluice skills list"))
    return out


# What an EMPTY list means, per role (#245). The sweep is generic over every
# list-typed field, and "empty" does not mean one thing across them, so before
# this it reported one thing anyway.
#
# `abstaining (empty)` is right for a preference gate in the #26/#63 sense: an
# unconfigured one passes every lead through. It is WRONG, in opposite
# directions, for the two other shapes present in the config today, and both
# were being labelled abstaining:
#
#   - `dossier_allow_hosts` is a security allowlist. Empty grants no exceptions,
#     which is the most restrictive state, not an absent opinion.
#   - `cv.slop_allow` SUBTRACTS from a hardcoded phrase list, so empty leaves
#     that list fully enforced. Its own field comment in `cv/config.py` already
#     names this ("NOT abstain-shaped ... the dossier_allow_hosts polarity").
#
# The cost of getting it wrong was paid in prose rather than in behaviour: the
# README carried six lines explaining that the output the reader was looking at
# meant two different things, naming two settings a new user has never heard of.
# A label needing six lines of README is the bug.
#
# The role is declared as dataclass field METADATA where the field itself is
# defined, never as a table here. A table in this module is a second copy of the
# field list and drifts the moment a knob is added; the metadata cannot, because
# it travels with the field. There is deliberately NO default: an unannotated
# field is REPORTED below rather than silently inheriting a posture, which is the
# whole failure this change exists to remove. (It used to raise; that broke doctor
# on a user's YAML, see `classify_gate`.) `tests/test_doctor.py` pins that
# every swept field declares one.
GATE_ROLES = {
    "abstain": "abstaining (empty)",
    "no_exceptions": "no exceptions granted (empty)",
    "no_normalisation": "nothing stripped (empty)",
}


def classify_gate(owner: str, name: str, value: list, role: str) -> ComponentCheck:
    """One posture NOTICE per list-typed field `list_typed_fields` swept from a
    loaded config: the empty posture for its ROLE, or active (non-empty).

    NOTICE for every DECLARED role, never DEGRADED, so an abstaining anything
    here (the shipped default, and legitimate -- the 672ad2a incident this whole
    invariant exists to prevent) never affects the exit code or reads as a
    problem, only as a fact worth knowing before a run. The single exception is
    the undeclared-role branch below, which is not a gate posture at all; see
    its own paragraph. That posture is unchanged by #245; what
    changed is that the row no longer says the same thing about three different
    meanings of empty.

    An unknown or missing role REPORTS rather than raises, and rather than picking a
    posture. An earlier cut raised here on the fail-loudly-at-construction principle, and
    that is the wrong principle for THIS call site: `doctor` is the command you run when the
    config is already wrong, so it must never refuse. Measured on the raising version, a user
    YAML slip putting a list on a non-gate field (`track.gmail_extra_query`) took
    `doctor --offline` from a full report and exit 1 to
    ZERO stdout and exit 2 -- the entire diagnostic destroyed by the field it was trying to
    describe, with a message blaming sluice's own metadata for the user's config.
    `load_track_config` states the rule this violated: a diagnostic that refuses to start is
    the opposite of a diagnostic.

    The sweep reaches here by runtime `isinstance`, so its roster is not the set of DECLARED
    gates -- a user can put a list on any field at all, across every sub-app loader. Measured,
    `cv.served_prefix` and `apply.neutral_name` both load a list and reach this function; the
    loaders' `refuse_wrong_container` rejects a SCALAR on a container field, which is the
    opposite direction and no help here. The fail-loudly property is still
    bought, at the right time and against the right audience:
    `test_every_swept_gate_declares_a_role` fails the BUILD when a real gate ships without a
    role, which is developer error and catchable before release. What arrives here at runtime
    is a user's config, and the honest row describes THEIR value, not sluice's metadata. It
    must never say `abstaining`, which is a claim about a preference gate this field is not.

    DEGRADED rather than NOTICE, and that is the one exception to the "always NOTICE" rule
    stated above. The rule protects an ABSTAINING gate from affecting the exit code -- the
    672ad2a class, where an unconfigured install must exit 0. A field holding a list it never
    declared is not an abstaining gate; it is a value of the wrong shape, and it breaks things
    downstream. Measured: `track.gmail_extra_query` as a list reaches
    `track/engine.py`'s `q + " " + cfg.gmail_extra_query` and raises
    `TypeError: can only concatenate str (not "list") to str`. Reporting that as NOTICE means
    `--strict` exits 0 on an otherwise-healthy install whose `track run` will crash, which is
    the no-silent-failures rule inverted. The developer case is meant to be caught before
    release rather than here: `test_every_swept_gate_declares_a_role` fails the build when a
    field the sweep reaches ships without a role, and `test_a_default_install_produces_no
    _degraded_gate_row` closes that guard's own gap by asserting the property through the real
    `Sluice.doctor` path -- the first is hand-listed, `app.py` builds its roster conditionally,
    and a config present in one and not the other would otherwise let a valid install fail
    `--strict`. With both, a DEGRADED row here describes a user's config in practice; it is not
    structurally unable to describe anything else."""
    subject = f"{owner}.{name}"
    if role not in GATE_ROLES:
        return ComponentCheck(
            "gates", subject, DEGRADED,
            f"holds a list of {len(value)}, but this setting does not take a list -- check "
            f"its type in your config; sluice cannot say what an empty one would mean here")
    if not value:
        return ComponentCheck("gates", subject, NOTICE, GATE_ROLES[role])
    return ComponentCheck("gates", subject, NOTICE, f"active: {len(value)} value(s)")
