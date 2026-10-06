"""sluice doctor: prove the pipeline is actually usable, not just configured.

Pure, zero-I/O core. The impure half -- resolving creds, building a provider,
running a one-token round-trip, constructing (but never writing through) the
store and renderer seams -- lives in `Sluice.doctor` (core/app.py); the
formatting and exit-code plumbing live in `cli.py`. This module owns only the
rules: what is configured (enumeration), and given a set of resolved facts
about one piece of it, is it ok / degraded / dead / awaiting setup / worth a notice
(classification).

Every stage uses ONE backend (#333: there is no fallback any more), so every backend a
stage uses blocks that stage when it cannot run. Two states answer "why not", and they
are the distinction #333's open question asked for: a keyless backend is `setup` --
unsupplied rather than broken (#243), so it exits 0 while still naming the capability it
stops -- and a backend whose credentials ARE present but whose round-trip fails is `dead`,
which does fail the exit code. Triage's tier-3 resolve backend is enumerated as a use of
its own, and only when `company_resolve_llm` is on: switched off, nothing runs on it.

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
# SETUP, a key that fails its round-trip is DEAD; a missing CV Layout is SETUP, an unreadable one
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
    """One (sub-app, role) pair that references a backend target. A single target can be
    referenced by several -- e.g. one claude-max backend shared by triage, cv and track.
    `role` is "backend" (the stage's one backend) or "resolve" (triage's tier-3 company
    resolution, #333); both block their sub-app, so the role only labels the display."""
    subapp: str
    role: str  # "backend" | "resolve"


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
    # D14 (#364/#365/#368): a DEGRADED row that blocks nothing but is worth reading by
    # default -- a CV that composes while leaving some of the user's work off, or a check
    # gone quiet. Without it the default view folds the row into "N more degraded".
    # `--require` reads `blocks` alone, so this can never make a row block; `--strict` fails
    # on it as on any DEGRADED row.
    warn_by_default: bool = False

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
        if self.warn_by_default and self.state != DEGRADED:
            raise ValueError(
                f"ComponentCheck({self.component}/{self.subject}) sets warn_by_default on a "
                f"{self.state!r} row; only a DEGRADED row is printed as a warning")
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
    # DEGRADED rows that block nothing but carry `warn_by_default` (#364 D14).
    warning_rows: list = field(default_factory=list)


@dataclass
class DoctorReport:
    checks: list  # list[BackendCheck]
    components: list = field(default_factory=list)  # list[ComponentCheck]

    def exit_code(self, *, strict: bool = False) -> int:
        """Non-zero iff a run-blocking backend or component is dead. `--strict`
        additionally fails on any degraded one (the cron mode for a user who wants a
        run that works but not properly to page them).

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
        with an EMPTY `blocks` still blocks nothing.
        Nothing here re-derives a state: it reads the classifiers' verdicts and groups
        them, so the verdict and the table can never disagree about a row.

        Every backend row blocks every sub-app it serves (#333). There used to be a
        degradable FALLBACK role whose failure blocked nothing; with one backend per
        stage, a backend that cannot run stops each stage that names it.
        """
        blockers: dict = {name: [] for name, _ in CAPABILITIES}
        for c in self.checks:
            if c.state in (DEAD, SETUP):
                # A `uses` naming a sub-app outside the roster would silently drop its
                # blocker on the floor; `tests/test_doctor_verdict.py` sweeps both
                # producers (this module's `blocks=` tuples and `enumerate_targets`'
                # specs) against CAPABILITIES so that cannot happen unnoticed.
                for u in c.target.uses:
                    if u.subapp in blockers:
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
                                               if getattr(c, "blocks", ())],
                       warning_rows=[c for c in degraded_rows
                                     if getattr(c, "warn_by_default", False)
                                     and not getattr(c, "blocks", ())])

    def awaiting_setup(self) -> list:
        """Every row the user has not supplied yet, in report order (#243).

        Derived, never hand-listed: the classifiers decide what is SETUP at the point they
        know why a thing is missing, and this reads that back. A roster here would be a second
        opinion about the same fact, and the two would drift."""
        return [c for c in self.checks + self.components if c.state == SETUP]


def enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list:
    """Every sub-app's ONE backend, plus triage's tier-3 resolve backend when it is on,
    deduped by (provider, model, host, claude_path).

    Apply is absent: it is offline by contract and has no backend. The resolve use mirrors
    `Sluice.triage()` exactly -- `resolve_backend` or triage's own `backend`, and a blank
    model when only `resolve_backend` was named (that provider's default, since triage's
    `model` is an id in another provider's namespace) -- so doctor probes what a real run
    actually builds. It shares triage's claude-max host/path, as the real construction does.

    Effort is deliberately NOT part of the dedup key: it changes cost/quality, not whether
    the backend works, so triage(medium)+cv(max) fold into one claude-max probe. A
    per-sub-app MODEL override does split, preserving the per-sub-app "is this a live model
    id" check. `claude_path` IS in the key so two claude-max backends pointing at different
    binaries never collapse.

    `cv_cfg` may be `None` -- `Sluice.doctor` passes that when `load_cv_config()` itself
    raised (#133/#107). cv's spec is simply OMITTED then, rather than substituted with a
    placeholder: triage's and track's backends are unrelated to cv's config and must still
    be checked, and building a bare `CvConfig()` here is exactly what
    tests/test_config_paths.py's test_no_production_code_builds_a_sub_app_config_directly
    forbids anywhere outside a sub-app's own loader.
    """
    specs = [
        # (subapp, role, provider, model, host, claude_path)
        ("triage", "backend", triage_cfg.backend, triage_cfg.model,
         triage_cfg.claude_max_host, triage_cfg.claude_max_path),
    ]
    if cv_cfg is not None:
        # Kept in its ORIGINAL triage/cv/track position rather than appended at the end: a
        # shared target's `uses` list is built in spec-iteration order, and `format_roles`
        # prints sub-apps in that same order.
        specs.append(("cv", "backend", cv_cfg.backend, cv_cfg.model,
                      cv_cfg.compose_host, cv_cfg.compose_claude_path))
    specs.append(("track", "backend", track_cfg.backend, track_cfg.model,
                  track_cfg.claude_max_host, track_cfg.claude_max_path))
    if triage_cfg.company_resolve_llm:
        specs.append((
            "triage", "resolve", triage_cfg.resolve_backend or triage_cfg.backend,
            triage_cfg.resolve_model or ("" if triage_cfg.resolve_backend
                                         else triage_cfg.model),
            triage_cfg.claude_max_host, triage_cfg.claude_max_path))
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
        # SETUP, not DEAD (#243): an unset key is a credential the user has not supplied,
        # not one that fails. A key that IS set and fails its round-trip falls through to
        # `probe_error` below and stays DEAD, which is the distinction that matters to a
        # monitor -- "not configured yet" is not an incident.
        return BackendCheck(target, SETUP, f"{key_var} unset")
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
    """The sub-apps a target serves, for display: "triage, cv, track", with triage's tier-3
    resolve use named separately ("...; resolve: triage") since it is a different job."""
    by_role: dict = {}
    for u in uses:
        by_role.setdefault(u.role, []).append(u.subapp)
    parts = []
    if by_role.get("backend"):
        parts.append(", ".join(by_role["backend"]))
    if by_role.get("resolve"):
        parts.append(f"resolve: {', '.join(by_role['resolve'])}")
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


def classify_store(facts: dict | None, *, cv_asks_for_bullets: bool = True) -> list:
    """`facts` is the store's own `preflight()` result (see core/protocols.py),
    or None when the configured store does not implement the optional method --
    reported as nothing rather than an error, because `Store.preflight` is an
    OPTIONAL member of the seam (reached through `getattr` in core/app.py). A store
    that cannot say is not a store that is broken.

    A missing vault BLOCKS (#243: SETUP when the user has not supplied one, DEAD
    when a named vault is gone): every sub-app that touches `self.store()` --
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
            # `read_evidence("experience")` RAISES rather than returning [], so
            # `cv/engine.py::missing_prerequisites` refuses the whole run before any spend
            # ("cannot read your experience entries"). Keyed on `cited_by_gate`, NOT on
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
        elif spec.names_in_skills_pool:
            # D12 (#364/#365/#368): a verified skill note's NAME may now appear in a CV's
            # skills list, so "framing only" would under-claim what verifying it buys. It
            # still licenses no figure; `citable` stays the experience kind's word alone.
            detail = (f"{verified} verified / {total} total entries -- framing for the CV "
                      f"composer, and each verified entry's name (its Label:, else its "
                      f"title) can appear in a CV's skills list")
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
        # A headings-only CV Layout (every role at bullets_max 0, #364 spec §5.2/D10) cites
        # nothing, and `cv run`'s prerequisite check accepts it with an empty corpus, so
        # the row must not block `cv` then -- `cv_asks_for_bullets` is the caller's reading
        # of core/layout.py::asks_for_bullets, the same predicate that check uses. It
        # defaults to True: with no layout read (absent, malformed, unreadable) the
        # cv_layout row already blocks, and this row keeps saying what it always said.
        if spec.cited_by_gate and not verified and not fact_missing and not cv_asks_for_bullets:
            out.append(ComponentCheck(
                "store", label, NOTICE,
                detail + " -- your CV Layout asks for no bullets, so cv run composes "
                "headings-only CVs without one"))
        elif spec.cited_by_gate and not verified and not fact_missing:
            out.append(ComponentCheck(
                "store", label, SETUP,
                detail + " -- cv run refuses to compose without at least one",
                blocks=("cv",)))
        else:
            out.append(ComponentCheck("store", label, NOTICE, detail))
    candidate_error = facts.get("candidate_error")
    if candidate_error:
        # DEAD, not SETUP: the profile EXISTS but the store could not read it, which is a
        # fault to fix rather than a note to write -- and an unreadable note is never read
        # as a blank one. It blocks cv for the reason the SETUP row below does: cv/engine.py
        # derives the name and contact block from this read, and it raises.
        out.append(ComponentCheck(
            "store", "Candidate Profile", DEAD, f"cannot be read -- {candidate_error}",
            blocks=("cv",)))
    elif not (facts.get("candidate_name_present") and facts.get("candidate_contact_present")):
        out.append(ComponentCheck(
            "store", "Candidate Profile", SETUP,
            "no name or no contact details -- cv run refuses to compose "
            "(skipped-config) before any backend call", blocks=("cv",)))
    else:
        out.append(ComponentCheck("store", "Candidate Profile", OK, "found"))
    return out


def classify_track_google(*, available: bool, import_error: str | None,
                           token_present: bool, token_path: str = "",
                           legacy_token_path: str = "",
                           flow_available: bool = True,
                           flow_import_error: str | None = None) -> ComponentCheck:
    """`track run` reconciles Gmail + Calendar over `sluice/track/google_client.py`,
    which lazy-imports the google client libraries so the rest of sluice stays
    importable without them (`sluice/` is stdlib-only except for the named,
    deliberate exceptions -- see CLAUDE.md). DEGRADED only, never DEAD:
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
    cron alert fires on, not a tidy-up.

    `legacy_token_path` is #201's addition: a NOTICE, not a SETUP, reported once a good
    token is already in use elsewhere. `core/paths.py::resolve`'s own `_LEGACY` warning is
    keyed on the RESOLVED path not existing, so the run that mints a fresh token there --
    `job-sluice track auth`, itself #201 -- silently disarms that warning for good, and a
    credential left behind in the pre-XDG cwd location goes unremarked forever after. This
    row survives that disarming because it is keyed on the CALLER supplying a legacy path
    that still exists, not on the resolved path's absence. NOTICE rather than SETUP because
    nothing here blocks anything -- the install works, a good token is in use -- so it must
    not join `doctor`'s default view of rows demanding action; see `DoctorReport.exit_code`
    for why NOTICE stays out of the exit code and `--strict` alike.

    `flow_available` is `sluice/track/auth.py::probe_flow_available`'s verdict, RESOLVED by
    the caller and passed in -- never computed here, and never by importing `track.auth` at
    this module's scope, for the exact reason that module's own docstring states: an install
    holding the `google` extra but not `google-auth-oauthlib` (the entire pre-#201 `[google]`
    population, since `pip install -U job-sluice` never re-resolves extras) must keep getting
    the SAME `available`/`token_present` verdicts it always has, unaffected by whether a
    consent flow can be built. Defaulted `True` for the same reason `token_path` is defaulted
    -- so the many direct callers already in the suite are unaffected -- and it is read ONLY
    inside the `not token_present` arm below: when a token already exists nothing needs
    minting, so whether `track auth` COULD mint one changes neither the row's state nor its
    detail. Naming the missing PACKAGE rather than the missing extra, deliberately: Homebrew
    and Docker already bake `[google]` in, so "pip install 'job-sluice[google]'" would be
    wrong for exactly the population a probe skew reaches -- the same reasoning
    `probe_flow_available`'s own message already applies to itself.

    `flow_import_error` is that same call's SECOND element, threaded through for the reason
    `probe_flow_available` catches `(ImportError, OSError)` rather than `ImportError` alone:
    a missing NATIVE dependency underneath the package does not always raise ImportError, so
    a caller that keeps only `flow_available` misdiagnoses that case as the generic "not
    importable" on the one row built to name it. Defaulted `None` so the direct callers
    already in the suite, none of which supplies one, keep getting the pre-existing wording
    below verbatim."""
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
        if not flow_available:
            # The remedy the OTHER branch names cannot work here: `job-sluice track auth`
            # exists on this install (it ships with sluice itself), but it cannot MINT a
            # token without `google_auth_oauthlib`, which this install's probe says is not
            # importable. Naming the command anyway would send exactly the population this
            # branch exists for at a command that fails the moment they run it -- the wrong
            # remedy for the population that needs one most. No install instruction either
            # (see the docstring above): the population reaching this arm most often already
            # has `[google]` baked in via Homebrew or Docker, where the gap is a probe skew
            # rather than a missing extra.
            #
            # `flow_import_error`, when given, already carries the doc link
            # `probe_flow_available` appended to it, so it REPLACES the trailing "See
            # <url>" sentence rather than sitting beside it -- appending both would print
            # the same link twice. The fallback (no reason passed) keeps that sentence, for
            # the direct callers already in the suite that construct this row with
            # `flow_available=False` alone.
            reason = flow_import_error or (
                "google_auth_oauthlib is not importable. See "
                "https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md"
                "#google-access-for-track")
            return ComponentCheck(
                "track", "google_token.json", SETUP,
                f"google libs are importable but no token file exists yet{where}. "
                "`job-sluice track auth` exists but cannot mint one on this install: "
                f"{reason}",
                blocks=("track",))
        return ComponentCheck(
            "track", "google_token.json", SETUP,
            f"google libs are importable but no token file exists yet{where} -- "
            "`track run` cannot reach Gmail/Calendar until one does. Run "
            "`job-sluice track auth --client-secrets <your client_secret.json>`; see "
            "https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md"
            "#google-access-for-track for the Cloud console steps first",
            blocks=("track",))
    if legacy_token_path:
        # Reported, never acted on: doctor is the one command that must not refuse on a
        # relocated file, and this row is what survives after a mint at the resolved path
        # disarms `resolve`'s own _LEGACY notice.
        return ComponentCheck(
            "track", "google_token.json", NOTICE,
            f"a token is in use{f' at {token_path}' if token_path else ''}, but a legacy "
            f"credential is still at {legacy_token_path} -- sluice no longer reads it. "
            "Delete it once you are sure nothing else uses it.")
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


# --- #364/#365/#368: the CV Layout and what the structured CV reads --------------------
#
# Every row here reports counts, positions and the command that lists the entries -- never
# an entry title, a tool, a skill or a decoy -- because a DoctorReport reaches MCP clients
# whole. Pure: Sluice.doctor reads the store once and passes what it read.

def classify_cv_layout(layout, error=None) -> ComponentCheck:
    """The `store / cv_layout` row: the note every CV is assembled into (#364 spec §9.1).

    From ONE read, which Sluice.doctor makes in its own `try` (#259: one bad note never
    collapses the store rows), passing the parsed layout or the exception. It blocks `cv`
    in every state but OK, in step with `cv run`, which refuses before any spend when the
    note is absent, malformed or unreadable."""
    from sluice.core.protocols import CV_LAYOUT_RELPATH, LayoutError
    if isinstance(error, LayoutError):
        return ComponentCheck("store", "cv_layout", DEAD,
                              f"{CV_LAYOUT_RELPATH} is malformed: " + "; ".join(error.problems),
                              blocks=("cv",))
    if error is not None:
        # Unreadable is never reported as absent (#242). The reason goes through the one
        # path-free formatter the Vault's own `<kind>_error` facts use: a system OSError's
        # str() names the absolute path, and a DoctorReport reaches MCP clients whole.
        from sluice.core.vault import _unreadable_reason
        return ComponentCheck("store", "cv_layout", DEAD,
                              f"{CV_LAYOUT_RELPATH} could not be read -- "
                              f"{_unreadable_reason(error)}",
                              blocks=("cv",))
    if layout is None:
        return ComponentCheck(
            "store", "cv_layout", SETUP,
            f"no CV Layout note at {CV_LAYOUT_RELPATH} -- every heading, date, location and "
            "title on a CV comes from it (docs/CONFIGURATION.md)", blocks=("cv",))
    n = len(layout.roles)
    return ComponentCheck("store", "cv_layout", OK, f"{n} role{'' if n == 1 else 's'}")


def classify_tools(experience_entries) -> list:
    """One DEAD row, blocking `cv`, when a verified experience entry's `Tools:` holds an
    item the gate cannot use (#364 spec §4.2): `cv run` refuses before any spend then, naming
    the entry and the item on the user's own terminal; this row counts them."""
    from sluice.core.tokens import tool_items
    bad = 0
    for entry in experience_entries:
        try:
            tool_items(entry)
        except ValueError:
            bad += 1
    if not bad:
        return []
    return [ComponentCheck(
        "store", "Experience Library (Tools)", DEAD,
        f"{bad} verified entr{'y' if bad == 1 else 'ies'} declare{'s' if bad == 1 else ''} a "
        "Tools: item the CV gate cannot use -- every word of a tool's name must begin with "
        "a letter (job-sluice experience list)", blocks=("cv",))]


def classify_cv_eligibility(layout, experience_entries) -> list:
    """D14 rows: verified entries no CV can cite (#364 spec §4.3, D6).

    `unmatched` -- a Company: matching no role's employers (a role's heading when it lists
    none), nor any_role or
    omitted -- and `blank` -- no Company: at all -- are citable nowhere, and while some
    other entry is citable the CV still composes. So each is DEGRADED, blocks nothing, and
    is listed by default: a user who verified an entry and never sees its work on a CV is
    owed the reason. `omitted` is the user's own choice and draws nothing.

    When NO slot can cite anything while the layout asks for bullets, `cv run` refuses the
    whole run (cv/engine.py::missing_prerequisites), so a row that blocked nothing would
    call the install fine about the exact thing that stops the next command. That case adds
    one DEAD row blocking `cv`, decided by the SAME core/layout.py::no_citable_slot over the
    same build_slots the refusal runs, so the two cannot disagree. DEAD, not SETUP: the user
    supplied both the layout and the entries, and the pair does not work -- the
    "something broken" side of the #243 distinction. The warning counts stay beside it, as
    its causes; an all-omitted vault has none, which is why the row names omitted too."""
    from sluice.core.layout import build_slots, no_citable_slot, placement_counts
    counts = placement_counts(layout, experience_entries)
    rows = []
    # The ids are only labels: build_slots keys eligibility by them, exactly as the refusal
    # labels them.
    slots = build_slots(layout, [dict(e, id=str(i)) for i, e in enumerate(experience_entries)])
    if experience_entries and no_citable_slot(slots):
        n = len(experience_entries)
        rows.append(ComponentCheck(
            "store", "cv_layout (no citable entry)", DEAD,
            f"no role in the CV Layout can cite any of your {n} verified experience "
            f"entr{'y' if n == 1 else 'ies'}, so `cv run` refuses -- give each a Company: "
            "that equals one of a role's employers (its heading when it lists none), or list "
            "it under any_role, and take it off omitted if it is there "
            "(job-sluice experience list)", blocks=("cv",)))

    def pronoun(n):
        return "it" if n == 1 else "them"

    def entries(n):
        return f"{n} verified experience entr{'y' if n == 1 else 'ies'}"

    if counts.get("unmatched"):
        n = counts["unmatched"]
        rows.append(ComponentCheck(
            "store", "cv_layout (not on your CV)", DEGRADED,
            f"{entries(n)} name{'s' if n == 1 else ''} a Company: that matches no role's "
            "employers (a role's heading when it lists none), nor "
            "any_role or omitted, in the CV Layout, so no CV can cite "
            f"{pronoun(n)} -- add the company to a role, or to omitted if leaving it off is "
            "deliberate "
            "(job-sluice experience list)", warn_by_default=True))
    if counts.get("blank"):
        n = counts["blank"]
        rows.append(ComponentCheck(
            "store", "cv_layout (no company)", DEGRADED,
            f"{entries(n)} ha{'s' if n == 1 else 've'} no Company:, so no CV can cite "
            f"{pronoun(n)} -- give {'it' if n == 1 else 'each'} the company it happened at "
            "(job-sluice experience list)", warn_by_default=True))
    return rows


def classify_skill_labels(named_entries, kinds) -> list:
    """An upgrade warning: verified notes whose names a CV's SKILLS section may list
    (`kinds`, the `names_in_skills_pool` kinds) that carry no `Label:`.

    A CV shows such a note under `Label:`, else its title (cv/selection.py::cv_name). Since
    4.0 `skills add` keeps the typed name in `Label:` because the filename it writes is a
    slug, so a note it made BEFORE 4.0 has no `Label:` and reaches a CV under its slug.
    Nothing is broken -- the CV still composes -- so DEGRADED, blocking nothing, listed by
    default: the user cannot otherwise learn why a skill reads oddly until a CV goes out.
    A COUNT and the listing command only, never a title: a report reaches MCP clients whole.
    The blank test is cv_name's own, so the two cannot disagree about what is missing.

    Only a SLUG-SHAPED title counts: one `evidence_slug` -- the reduction `skills add` filed
    every pre-4.0 note under -- leaves unchanged. A note made by hand and titled with its
    real name ("Example Query") already reaches a CV under that name, so counting it would
    warn on every run, and fail `doctor --strict`, on a vault with nothing to fix. The
    reduction is IMPORTED, never restated, so this test cannot drift from what `add` wrote.
    A hand title that happens to be slug-shaped (a single lowercase word) is counted too:
    nothing local tells it from an `add` note, and it reaches a CV exactly as a slug would."""
    from sluice.core.vault import evidence_slug

    def slug_shaped(title):
        try:
            return evidence_slug(title) == title
        except ValueError:   # reduces to nothing usable, so `add` cannot have written it
            return False

    missing = sum(1 for e in named_entries
                  if not str((e.get("fields") or {}).get("Label") or "").strip()
                  and slug_shaped(str(e.get("title") or "")))
    if not missing:
        return []
    listers = " / ".join(f"job-sluice {kind} list" for kind in kinds)
    return [ComponentCheck(
        "store", "cv skills (no Label)", DEGRADED,
        f"{missing} verified skill note{'' if missing == 1 else 's'} ha"
        f"{'s' if missing == 1 else 've'} a slug for a title and no Label:, so a CV lists "
        f"{'it' if missing == 1 else 'each'} under the slug -- `skills add` named notes that "
        "way before 4.0; add `Label:` with the name as a CV should show it "
        f"({listers} shows which: a slug title with Label: (none))", warn_by_default=True)]


def classify_attribution(experience_entries) -> list:
    """A NOTICE (#364 spec §6.6, softened by the owner's model of 2026-10-06): no verified
    entry declares Tools:, so the misattributed-tool check is off, while some verified entry
    declares Skills:. A vault with neither field is an unconfigured install and draws nothing
    (empty config abstains).

    NOTICE rather than a default-view warning: `Tools:` holds the specific tools and hard
    skills tied to a job and `Skills:` the general soft skills tied to none, so a vault that
    declares only soft skills is a legitimate shape, not a fault -- the row is information
    for `--verbose`, blocks nothing and never reaches the exit code, `--strict` included.
    `cv run` logs nothing for it either. Count-only, never a value -- a DoctorReport reaches
    MCP clients whole."""
    from sluice.core.tokens import skill_items, tool_items

    def declares(entry):
        try:
            return bool(tool_items(entry))
        except ValueError:
            return True   # an unusable item is classify_tools' DEAD row, not this one

    if any(declares(e) for e in experience_entries):
        return []
    annotated = sum(1 for e in experience_entries if skill_items(e))
    if not annotated:
        return []
    return [ComponentCheck(
        "store", "cv attribution check", NOTICE,
        f"off: no verified experience entry declares Tools:, though {annotated} "
        f"declare{'s' if annotated == 1 else ''} Skills: -- Tools: (specific tools and hard "
        "skills tied to the job) is what turns the check on, since each declared tool is "
        "checked in every bullet; Skills: holds general soft skills, offered for a CV's "
        "skills list only and never checked (docs/CONFIGURATION.md)")]


def classify_decoys(decoys, experience_entries, skill_names, layout) -> list:
    """The #364 spec §8 warning: a `cv.fabrication_decoys` entry matching, as a whole term, the
    user's own data -- a verified entry's Tools: or Skills: item (both feed the SKILLS pool,
    the latter since the owner decision of 2026-10-06), a verified skill's CV name, or any CV
    Layout text. The ban contradicts that data: it keeps the tool or skill off every CV's
    skills list, while layout text renders regardless. `skill_names` arrive already derived
    (Sluice.doctor passes cv/selection.py::cv_name's answers), so this and the pool agree.
    Decoys are named by POSITION, never echoed."""
    from sluice.core.layout import layout_strings
    from sluice.core.tokens import find_term, skill_items, tool_items

    def items(entry):
        try:
            return tool_items(entry)
        except ValueError:
            return []

    texts = ([t for e in experience_entries for t in items(e)]
             + [t for e in experience_entries for t in skill_items(e)] + list(skill_names)
             # Each layout string on its own: searching their join matched a phrase across
             # two of them, a warning about text the user never wrote.
             + (list(layout_strings(layout)) if layout is not None else []))
    hits = [i for i, d in enumerate(decoys, 1) if any(find_term(t, d) for t in texts)]
    if not hits:
        return []
    if len(hits) == 1:
        where, verb = f"entry {hits[0]}", "matches"
    else:
        where = "entries " + ", ".join(map(str, hits[:-1])) + f" and {hits[-1]}"
        verb = "match"
    return [ComponentCheck(
        "gates", "cv.fabrication_decoys", DEGRADED,
        f"cv.fabrication_decoys {where} {verb} your own Tools: or Skills:, a verified "
        "skill's name or your CV Layout -- it keeps that tool or skill off every CV's skills "
        "list, and "
        "layout text renders regardless; remove the decoy, or the data it contradicts",
        warn_by_default=True)]
