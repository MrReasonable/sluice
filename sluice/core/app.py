"""The composition root: `Sluice(config)`.

This is the half of the plugin story a registry cannot provide.

**Adapter plugins** are things core CALLS (store, fetcher, renderer, backend). A registry
covers those. **Surface plugins** are things that CALL core -- a web UI, a TUI, a daemon.
A registry does nothing for them. They need a programmatic API to drive, and until now
sluice had none: its API was `cli.py` functions with the signature `(args, config)` that
constructed their own `Vault()` and `Camofox()` and printed to stderr. A web UI could not
drive that, so "a web UI is a plugin" would have been a lie.

`Sluice` is the FIRST HALF of that API: it resolves every adapter the config names, so a
surface no longer has to construct a `Vault()` or a `Camofox()` for itself.

Backend ROLE-selection (`Sluice.backend`) has made the same move: `cli.py`'s
`_select_backend` and its provider-construction helpers now live here as
`Sluice.backend`, so a surface resolves a judge backend the same way it resolves a
store or a fetcher. The lazy dossier fetcher and the seen/lastrun file handling still
live in `cli.py`, and the operations themselves (triage, compose, prep, record, track)
are still driven from there. So a web UI written today would resolve its adapters
(store, fetcher, renderer, backend) through `Sluice` and then have to duplicate the
rest of `cli.py`'s wiring. Moving that wiring in -- and adding `Sluice.triage(...)`,
`.compose_cv(...)` and friends as value-returning methods -- is the follow-up that makes
"a surface is a plugin" true rather than nearly-true. It is deliberately a separate change:
it touches every command, and it deserves its own review.

Adapters are resolved LAZILY, on first use. That preserves the property `cli.py`'s
inside-the-function imports were protecting: an offline command must never construct a
browser, a store, or an LLM backend just by existing. `sluice triage run --no-llm` still
touches no backend; `sluice ingest list-sources` still touches no vault.
"""
import os

from sluice.core import plugins
from sluice.core.config import Config
from sluice.core.log import get_logger

_log = get_logger("app")

_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"

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

    def __init__(self, config=None, **overrides):
        # A composition root with no config uses the code defaults, exactly as the
        # adapters did when cli.py constructed them bare. Callers (and tests) that pass
        # None must get a working Sluice, not an AttributeError deep inside a factory.
        self.config = config if config is not None else Config()
        self._overrides = {k: v for k, v in overrides.items() if v is not None}
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
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses."""
        from sluice.core.dossier import DossierCache
        cam = {}
        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
            if url:
                if "client" not in cam:
                    cam["client"] = self.fetcher()
                c = cam["client"]
                tid = c.create_tab(url)
                if tid:
                    res = c.evaluate(tid, "document.body.innerText")
                    md = res.get("result") if isinstance(res, dict) else ""
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}
        return DossierCache(dossier_dir, ttl_days, fetcher=fetch)

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
        import os
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
        from sluice.cv.engine import run_batch, run_one
        from sluice.core.leads import slug_matches

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
        return [run_one(notes[0], store, cvcfg, backend, cache, renderer=renderer,
                        dry_run=dry_run)]

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
    else:
        raise plugins.UnknownAdapter("seam", seam,
                                     [_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM])
