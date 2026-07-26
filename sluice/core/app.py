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
import os
from dataclasses import dataclass

from sluice.core import plugins
from sluice.core.config import Config
from sluice.core.log import get_logger

_log = get_logger("app")


@dataclass
class DedupeCluster:
    id: str
    members: list          # list[LeadNote]
    survivor: object       # LeadNote, or None on conflict
    conflict: bool
    flagged_losers: list   # losers carrying a CV/sign-off hold or an application-owned status


_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"
_BACKEND_SEAM = "backend"
# Every seam a constructor override may name. Used to reject a misspelled key at
# construction instead of dropping it silently (see Sluice.__init__).
_SEAMS = (_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM, _BACKEND_SEAM)

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
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except OSError:
        return set()


def _save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(sorted(seen)))


def _load_lastrun(path):
    """Read the ISO timestamp of the previous successful (non-dry-run) track run,
    so the next run's Gmail query can be scoped since then (F10) instead of the
    fixed lookback window. Missing/unreadable file just means "no prior run"."""
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


def _make_primary(name, model, *, effort, host, claude_path):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    return make_backend(name, model, api_key=api_key, base_url=base_url,
                        effort=effort, claude_host=host, claude_path=claude_path)


def _make_fallback(name, model):
    """Build the fallback leg, or None when its credentials are absent.

    A missing key is not fatal: running primary-only (a claude-max setup with no
    per-token key configured) is legitimate and must keep working. But it *is* a
    degraded state -- the run has no safety net if the primary dies -- so warn
    loudly at build time rather than letting it surface as a 401 at the exact
    moment the primary goes down. When the fallback is explicitly *selected*
    (`--backend fallback`) there is nothing to degrade to, so make_backend's
    missing-key error is allowed to propagate; see Sluice.backend."""
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    if name in _PROVIDER_ENV and not api_key:
        _log.warning(
            "fallback backend '%s' has no API key (%s unset): running with no "
            "fallback -- a primary failure will now fail the run",
            name, _PROVIDER_ENV[name][0])
        return None
    return make_backend(name, model, api_key=api_key, base_url=base_url)


def _make_fallback_strict(name, model):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    return make_backend(name, model, api_key=api_key, base_url=base_url)


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
        live (`cv.renderer`, `cv.render_script`, ...)."""
        return self._resolve(_RENDERER_SEAM, getattr(cvcfg, "renderer", "script"), cvcfg)

    def backend(self, role, *, primary_name, primary_model, effort, host, claude_path,
                fallback_name, fallback_model):
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
            return _make_fallback_strict(fallback_name, fallback_model)
        primary = _make_primary(primary_name, primary_model, effort=effort, host=host,
                                claude_path=claude_path)
        if role == "primary":
            return primary
        fallback = _make_fallback(fallback_name, fallback_model)
        return FallbackBackend(primary, fallback) if fallback else primary

    def dossier_cache(self, dossier_dir, ttl_days):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text read via
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses.

        The lead url comes off a scraped listing, so it is guarded (#18): checked before
        a tab is opened, and the LANDED url re-checked before the body is read. A refusal
        RAISES rather than returning an empty dossier -- see the comment on the raise.
        """
        from sluice.core.dossier import DossierCache
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
                    res = c.evaluate(tid, "location.href")
                    landed = res.get("result") if isinstance(res, dict) else None
                    if not isinstance(landed, str):
                        _refuse(urlguard.LANDED_UNREADABLE, pre.host)
                    if not landed or landed == "about:blank":
                        _refuse(urlguard.NOT_SETTLED, pre.host)
                    post = urlguard.check_url(landed, allow_hosts=allow, resolve=resolve)
                    if not post.allowed:
                        _refuse(urlguard.LANDED_BLOCKED, post.host)
                    # Only now is the body safe to pull into memory.
                    body = c.evaluate(tid, "document.body.innerText")
                    md = body.get("result") if isinstance(body, dict) else None
                    if not isinstance(md, str):
                        # Same reasoning as no-tab: a non-string body used to become a
                        # cached empty JD indistinguishable from a real empty one.
                        _refuse(urlguard.BODY_UNREADABLE, pre.host)
                finally:
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}

        return DossierCache(dossier_dir, ttl_days, fetcher=fetch)

    def ingest(self, sources, *, dry_run=False, json_sink=False, out=None):
        """Run the ingest sub-app: fetch each given source, dedup/relevance-gate
        through `ingest.engine.run`, and write survivors to a sink. Which sources
        to run (the enabled/disabled overlay, `--source`/`--all`) stays in
        cli.py's `_selected` -- this method just executes the list it is handed.

        `dry_run` OR `json_sink` both route to `JsonSink`, never `VaultSink`: a
        dry run's whole point is to change nothing on disk, and `--sink json` is
        an explicit request to skip the vault -- so both must skip the store and
        seen.db entirely rather than constructing them and then merely not
        calling write(). `out` lets a caller (a future surface, or a test) capture
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
        ctx = Ctx(camofox=self.fetcher(), config=self.config, sleep=self._sleep)
        seen = SeenDb()
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

    def triage(self, *, statuses=("new", "research"), limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        """Run the triage sub-app end to end: classify, dossier-enrich the kept leads,
        judge them, and write the audit trail. `no_llm` skips backend construction
        entirely (`triage()`'s deterministic classify-only path), preserving the
        offline guarantee `--no-llm` has always given `sluice triage run`.

        The primary/fallback field mapping here (`claude_max_*` for primary,
        `cheap_model` for fallback) is triage's own config shape -- other sub-apps
        (cv, apply) have their own `*Config` with their own field names, so this
        mapping is NOT shared and belongs in this method, not in `Sluice.backend`."""
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        tcfg = load_triage_config()
        audit = AuditLog(os.environ.get("TRIAGE_AUDIT", "./triage-audit.jsonl"))
        backend = None if no_llm else self.backend(
            backend_role, primary_name=tcfg.primary_backend,
            primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort,
            host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path,
            fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)
        cache = self.dossier_cache(os.environ.get("DOSSIER_DIR", "./dossiers"),
                                  tcfg.ttl_days)
        return _triage_run(self.store(), tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm)

    def compose_cv(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False,
                    no_serve=False, backend_role="auto"):
        """Run the cv sub-app: compose (and, unless dry_run, render) a CV for one
        shortlisted lead or for every shortlisted lead. Returns the list of CvResult.

        The renderer is resolved ONLY when not dry_run: a missing render script is a
        config error that must surface at construction, before any LLM spend -- but a
        dry run's whole point is to cost nothing and change nothing, so it must not
        require a renderer to exist at all. This is the fabrication-gate-safe behaviour
        `cli.py`'s old cmd_cv_run preserved; moving it here must not lose it.

        cv's config maps to Sluice.backend's fields via compose_model/compose_effort/
        compose_host/compose_claude_path -- NOT triage's claude_max_* fields. That
        mapping belongs here, not in Sluice.backend, same reasoning as `triage()`."""
        from sluice.cv.config import load_cv_config
        from sluice.cv.engine import CvResult, run_batch, run_one
        from sluice.core.leads import slug_matches
        from sluice.core.protocols import VaultConflict

        cvcfg = load_cv_config()
        if no_serve:
            cvcfg.served_dir = ""  # engine still renders; serve is skipped when dir is empty
        renderer = None if dry_run else self.renderer(cvcfg)
        backend = self.backend(
            backend_role, primary_name=cvcfg.primary_backend,
            primary_model=cvcfg.compose_model, effort=cvcfg.compose_effort,
            host=cvcfg.compose_host, claude_path=cvcfg.compose_claude_path,
            fallback_name=cvcfg.fallback_backend, fallback_model=cvcfg.cheap_model)
        cache = self.dossier_cache(cvcfg.dossier_dir, cvcfg.ttl_days)
        store = self.store()

        if all_shortlist:
            return run_batch(store, cvcfg, backend, cache, renderer=renderer,
                             limit=limit, dry_run=dry_run)
        notes = [n for n in store.read_leads({"shortlist"}) if slug_matches(n, lead)]
        if not notes:
            return []
        # The direct single-lead path overwrites an existing tailored_cv (guard_existing_cv
        # defaults False, unlike run_batch) -- a user re-tailoring one lead by name means it.
        # A lead HELD for sign-off (pending_cv set) is the exception: run_one skips it before
        # compose (#60 latch), so re-running `cv --lead` will NOT re-tailor it -- the
        # sanctioned way to force a fresh compose of a held lead is `cv signoff --discard`.
        # Under sustained write-race exhaustion set_tailored_cv still raises VaultConflict
        # (#16); that must not escape to the CLI as an unhandled traceback.
        try:
            return [run_one(notes[0], store, cvcfg, backend, cache, renderer=renderer,
                            dry_run=dry_run)]
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

    def sign_off_cv(self, *, lead, accept=True, confirm=None):
        """Resolve a shortlisted lead by slug ONCE and resolve its #60 sign-off hold via the
        store: accept -> promote pending_cv to the send-ready `tailored_cv` pointer;
        `accept=False` (discard) -> clear the markers, freeing a fresh compose.

        `confirm`, when given, is called with (slug, pending_cv, claims) AFTER the lead is
        resolved and BEFORE the store write -- so the CLI can show the flagged claims and
        prompt while this method itself does no I/O; a falsey return aborts. Resolving once
        and handing `note.ref` straight to the store means a separate peek and execute can
        never diverge onto different substring matches (a real risk when a slug matches two
        leads and the vault changes between reads).

        Returns (slug, outcome) where outcome is the store's OWN verdict
        (promoted|discarded|collision|nothing), 'aborted' (confirm declined), or 'conflict'
        (a sustained write race, #16, never an unhandled traceback) -- or None if no lead
        matched."""
        import json

        from sluice.core.leads import slug_matches
        from sluice.core.protocols import VaultConflict
        store = self.store()
        notes = [n for n in store.read_leads({"shortlist"}) if slug_matches(n, lead)]
        if not notes:
            return None
        note = notes[0]
        pending = note.fm.get("pending_cv") or ""
        if not pending:
            return note.slug, "nothing"
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
                return note.slug, "aborted"
        try:
            return note.slug, store.sign_off(note.ref, accept=accept)
        except VaultConflict as e:
            _log.warning("cv signoff for %s lost the write race: %s", note.ref, e)
            return note.slug, "conflict"

    def prep(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False):
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
        if all_shortlist:
            return engine.preview_all(store, cfg, limit=limit)
        if dry_run:
            note, reason = select.select_one(store, lead, cfg)
            if note is None:
                return [engine.PrepResult(lead=lead, status="skipped", reason=reason)]
            pkt = packet.build_packet(note, cfg, cv_staged=False)
            return [engine.PrepResult(lead=lead, status="previewed", packet=pkt)]
        return [engine.prep_one(store, cfg, lead)]

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

        tcfg = load_track_config()
        lastrun_path = tcfg.seen_db + ".lastrun"
        seen = _load_seen(tcfg.seen_db)
        deadletter = DeadLetterDb(deadletter_path(tcfg.seen_db))
        since_iso = _load_lastrun(lastrun_path)
        client = client if client is not None else RealGoogleClient(tcfg.token_path)
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
            if not rep.auth_error and not rep.deadletter_error:
                _save_lastrun(lastrun_path, now_iso)
        return rep

    def track_confirm(self, *, lead, to, when=None, dry_run=False):
        """Run the track sub-app's confirm step: apply an operator-approved
        proposal (a status advance the engine flagged rather than auto-applied),
        clearing that lead's dead-letter entries on a successful advance."""
        from sluice.track import engine as track_engine
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        tcfg = load_track_config()
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
        tcfg = load_track_config()
        dl = DeadLetterDb(deadletter_path(tcfg.seen_db))
        if dry_run:
            entries = dl.open_entries()
            n = sum(1 for e in entries
                    if (message_id is not None and e.message_id == message_id)
                    or (lead is not None and e.lead == lead))
            return {"cleared": n, "dry_run": True}
        n = dl.clear_id(message_id) if message_id is not None else dl.clear_lead(lead)
        return {"cleared": n, "dry_run": False}

    def doctor(self, *, offline=False, probe=None):
        """Preflight every configured backend (primary + fallback, per sub-app):
        is the provider known, is a model resolved, are the credentials present
        in THIS process, and -- unless `offline` -- does a one-token round-trip
        succeed? Returns a DoctorReport whose `exit_code` is non-zero when a
        run-blocking backend is dead.

        Backends only: this method touches neither `self.store()` nor
        `self.fetcher()`, so `sluice doctor` never constructs a browser or a
        store. `probe` is the test seam -- a `callable(backend) -> None` that
        raises `BackendError` on failure; it defaults to the real round-trip.
        The provider is built DIRECTLY via `make_backend` (not the role
        composite), so there is no `FallbackBackend` to disentangle, and it is
        built ONLY when there is something testable -- a known provider whose
        credentials are satisfied -- so a keyless per-token backend is
        classified from config alone, never by catching a construction error."""
        import shutil
        import time

        from sluice.core import doctor as _doctor
        from sluice.core.backends import DEFAULT_MODELS, BackendError, make_backend
        from sluice.cv.config import load_cv_config
        from sluice.track.config import load_track_config
        from sluice.triage.config import load_triage_config

        targets = _doctor.enumerate_targets(
            load_triage_config(), load_cv_config(), load_track_config())
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
            # A local (no-host) backend that needs no key IS the claude-max CLI;
            # for the offline mode we can only check the binary exists on PATH.
            cli_present = None
            if offline and known and not needs_key and not t.host:
                cli_present = shutil.which(t.claude_path) is not None
            # Round-trip ONLY when live AND buildable+testable: known provider,
            # creds satisfied. Everything else is classified from config alone.
            probe_error = None
            elapsed = None
            if not offline and known and (not needs_key or key_present):
                try:
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
        return _doctor.DoctorReport(checks=checks)

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
