"""Layered configuration: code defaults < sluice.yaml < environment.

Every knob has a sane default so sluice runs with no config file at all; a
sluice.yaml overrides pieces of it; env vars win last so ops and offline tests
can override without editing files.
"""
import os
from dataclasses import dataclass, field

from sluice.core.leads import LEAD_LAYOUTS
from sluice.core.paths import config_file
from sluice.core.urlguard import parse_allow_hosts

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

# NB no root `locations` here: it was a DEAD key (declared, documented in
# sluice.yaml.example, read by nothing) and is retired outright by #8. The comment it
# carried called it "a loaded gun rather than a live bug, since the first consumer to
# wire it into a search or a gate would have inherited a stranger's 'remote only'".
# `sluice init` would have been that consumer -- a wizard asking for geography and
# writing it into a key nothing reads -- so `refuse_retired_locations` RAISES on it
# rather than letting it be dropped in silence. Geography lives at
# `triage.target_locations`, which is live.


@dataclass
class SourceConfig:
    enabled: bool = True
    tuning: dict = field(default_factory=dict)
    # Optional per-source search override: [[label, url, {params}?], ...]. When set,
    # it replaces the source's built-in example searches, so a user keeps their own
    # (personal) search list in config rather than in the code. Empty = use built-in.
    searches: list = field(default_factory=list)


@dataclass
class Config:
    sources: dict = field(default_factory=dict)  # id -> SourceConfig
    # Which implementation fills each adapter seam. Selection is by NAME, and an unknown
    # name raises at construction listing the valid ones -- it never falls through to a
    # default. For the store seam especially, a quiet wrong default means writing the
    # user's leads somewhere they did not ask for.
    store: str = "vault"
    fetcher: str = "camofox"
    # Where the store keeps the baseline CV. A STORE location, so it lives here: once the
    # store is resolved from the root Config, a `cv.baseline_rel` could not reach the store
    # that has to honour it. It used to work only because cv/engine.py passed it down by
    # hand (`vault.read_baseline(cvcfg.baseline_rel)`), which is the coupling this seam
    # exists to remove. Moving it silently would have been the worse bug -- a user pointing
    # at a curated baseline would get a stale one, with the fabrication gate still green --
    # so load_cv_config RAISES on the old key rather than dropping it.
    baseline_rel: str = "My CV/CV.md"
    # Where the vault lives (#80). It had no config key at all before, so it was
    # settable only by a VAULT_DIR env var that does not survive a new shell. Blank
    # means UNSET: stores/vault.py:_make does `env or this or None` and lets
    # Vault.__init__ keep supplying ./vault, so the store still owns its own default.
    # Unlike everything else #80 swept, the vault deliberately does NOT relocate to an
    # XDG root -- it is the user's Obsidian directory, their data, not sluice's state.
    vault_dir: str = ""
    # The dossier cache directory (#80). ONE root key, replacing the two sub-app keys
    # (triage.dossier_dir, cv.dossier_dir) that both defaulted to the same ./dossiers
    # literal -- so the cache was shared only by coincidence of that literal. Moving
    # one and not the other splits it and cv re-fetches every dossier over the live
    # SSRF-guarded network path, so the sharing is now structural rather than
    # tested-for. Lives on the root Config for the same reason dossier_allow_hosts
    # does: dossier_cache is called from BOTH sub-apps. Blank means UNSET -- resolution
    # is `env or config key or XDG`, and a non-empty default here short-circuits that
    # chain so the XDG location is never reached.
    dossier_dir: str = ""
    # Hosts/CIDRs exempt from the dossier fetcher's SSRF guard (#18). A SAFETY
    # allowlist, not a preference gate: empty means "no exceptions granted", NOT
    # "match nothing" -- an unconfigured install still fetches every public url,
    # because the address rule admits them, not this list. Lives on the root
    # Config, not TriageConfig/CvConfig, because dossier_cache is called from BOTH
    # sub-apps and a security policy that differs between them is a bug.
    dossier_allow_hosts: list = field(default_factory=list)
    notify: dict = field(default_factory=dict)
    # Coarse ingest title filter. Personal, so empty by default: an unconfigured
    # gate passes everything through rather than applying someone else's taste.
    relevance_keep: list = field(default_factory=list)
    relevance_drop: list = field(default_factory=list)
    # Words that decorate a location without locating it, subtracted before #5 compares
    # two postings for a split. Empty by default -> nothing subtracted (abstain).
    location_noise_words: list = field(default_factory=list)
    # Title-noise tokens stripped before #23's dedup clustering compares two roles. Empty by
    # default -> strictest clustering (nothing stripped), erring toward NOT merging (safe).
    dedupe_title_noise_words: list = field(default_factory=list)
    # Days since a lead was last seen in a scrape before it counts as stale (#9). 0 = OFF,
    # and off is the shipped default: "stale" is a judgement, and a shipped non-zero would
    # bin leads on a stranger's idea of it -- the 672ad2a class, where a preference baked
    # into source silently discarded someone's whole job hunt. Lives on the ROOT Config
    # because `leads expire`, cv and apply all read it, and a staleness policy that
    # differed between them would be a bug. NB `ttl_days` (cv/config.py, triage/config.py)
    # is the unrelated DOSSIER CACHE ttl; this name is deliberately distinct from it.
    lead_ttl_days: int = 0
    # Which named folder layout the lead store files notes into (#1). "" = flat, exactly as
    # before this existed; "active_archive" = Active/ + Archive/. Lives on the ROOT Config for the
    # same reason `location_noise_words` does: `Sluice.store()` resolves the store from
    # `self.config`, so a key the STORE must honour cannot sit in a sub-app block. OFF by default
    # -- sluice does not own the layout, it offers one, and an unconfigured install must be
    # byte-identical to the flat store.
    lead_layout: str = ""

    def source(self, id: str) -> SourceConfig:
        """Config for a source id; unlisted sources default to enabled + no tuning."""
        return self.sources.get(id, SourceConfig())


def _str_list(value, name: str) -> list:
    """A list of strings from config, failing loudly. `None`/absent -> []. A YAML SCALAR
    (`location_noise_words: remote`) would otherwise `list()`-explode into single characters
    and silently mis-configure the gate; a clear error at construction is the house style
    (see _select_backend). Rejects a non-list and any non-string entry."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{name} must be a list of strings, got {value!r}")
    return list(value)


def sub_app_block(block: str, loaded: object) -> dict:
    """Normalise a sub-app's top-level block to a mapping, raising `ValueError` when it
    is anything else.

    Every sub-app loader reads its block as `(yaml.safe_load(f) or {}).get("<block>") or
    {}`, which is only a mapping when the user wrote one. Measured against a real config
    file, the four non-mapping spellings fail in three different, all-wrong ways:

      cv: "hello"        -> AttributeError: 'str' object has no attribute 'get'
      cv: [a, b]         -> AttributeError: 'list' object has no attribute 'get'
      cv: 5 / cv: true   -> TypeError: argument of type 'int' is not a container
      cv: my name is here -> ValueError, but the WRONG one -- `"name" in data` is a
                             SUBSTRING test on a str, so the #133/#107 migration guard
                             fires and tells the user to move a `cv.name` key they
                             never set.

    Two harms, and the third spelling is the worse of them. `doctor` is the command a
    user runs BECAUSE their config is wrong; it guards `load_cv_config()` with `except
    ValueError` precisely so a bad `cv:` block becomes a DEAD row rather than a
    traceback, and `AttributeError`/`TypeError` walk straight through that handler. And
    a wrong diagnosis is worse than a raw traceback: it sends the user to edit a key
    that is not there.

    Normalising HERE rather than widening `doctor`'s `except` clause is what fixes both
    at once -- with `data` guaranteed a mapping, every `"<key>" in data` membership test
    in every loader is a key lookup again rather than a substring match. It is applied
    to all four loaders, not just `cv`: they share the identical read, so guarding one
    would leave the same trap armed in three places (and `doctor` calls
    `load_triage_config`/`load_track_config` unguarded, where a clean `ValueError` is
    still a better failure than an `AttributeError` from deep inside a setattr loop).

    The message names the block and the TYPE found, never the value: a malformed `cv:`
    block usually contains whatever the user was mid-way through typing, and an
    exception travels further than the file it came from -- the same ruling
    `refuse_retired_dossier_dir` and `dossier_allow_hosts` already make.
    """
    if loaded is None or loaded == {}:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"the `{block}:` block of sluice.yaml must be a mapping of settings, but it "
            f"holds a {type(loaded).__name__}. Check the indentation under `{block}:` -- "
            f"each setting belongs on its own indented line, as `  key: value`.")
    return loaded


def refuse_retired_dossier_dir(block: str, data: dict) -> None:
    """Raise if a sub-app block still carries the retired `dossier_dir` key (#80).

    One helper rather than a raise written out in each loader, so the two cannot drift
    apart or one of them quietly not fire -- and a partial retirement is the bad case
    here, since it is what splits the shared cache.

    Both loaders filter unknown keys with `hasattr`, so without this the key is dropped
    in SILENCE: a user who had pointed cv at its own dossier directory would get a
    different one, with no signal and the fabrication gate still green. Fail loudly at
    construction, this repo's rule precisely because a quiet wrong default is the bug
    class it most consistently engineers out.

    The message names the key and its replacement and NEVER echoes the value. That
    differs deliberately from the `cv.baseline_rel` raise it is modelled on:
    `baseline_rel` is a store-RELATIVE name, while this is a host path usually under a
    home directory, and an exception travels further (logs, bug reports, pasted
    tracebacks) than the config file it came from. `dossier_allow_hosts` above already
    rules that way for the same reason.
    """
    if "dossier_dir" in data:
        raise ValueError(
            f"{block}.dossier_dir has moved to the top level of sluice.yaml. triage and "
            f"cv share ONE dossier cache, and two keys could split it -- with cv then "
            f"re-fetching every dossier over the network. Move it out of the `{block}:` "
            f"block to a root `dossier_dir:` key.")


def refuse_retired_locations(data: dict) -> None:
    """Raise if a config still sets the retired root `locations` key (#8).

    Declared, documented in `sluice.yaml.example`, and read by NOTHING -- the comment it
    carried called it "a loaded gun rather than a live bug, since the first consumer to
    wire it into a search or a gate would have inherited a stranger's 'remote only'".
    `sluice init` would have been that consumer, so the key is retired rather than
    finally populated.

    BOTH spellings, because the loader also honoured `$SLUICE_LOCATIONS`. Raising on the
    file while staying silent on the environment is precisely the asymmetry the
    fail-loudly rule exists to remove: a user who set geography in their shell would
    watch it quietly stop being read, which is the same silent-wrong-default bug class
    this codebase most consistently engineers out.

    The VALUE is never echoed. Geography is personal, and an exception travels further
    (logs, bug reports, pasted tracebacks) than the config file it came from -- the same
    ruling `refuse_retired_dossier_dir` and `dossier_allow_hosts` already make.
    """
    if "locations" in data or "SLUICE_LOCATIONS" in os.environ:
        raise ValueError(
            "the root `locations` key (and $SLUICE_LOCATIONS) was read by nothing and "
            "has been retired. Geography is a triage concern -- move your value to the "
            "`target_locations:` key inside the `triage:` block, and `unset SLUICE_LOCATIONS` "
            "if you exported it.")


def load_config(path: str | None = None) -> Config:
    data = {}
    path = path or config_file()
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Before any field is read, so a retired key is reported rather than dropped in the
    # silence a `data.get` would give it.
    refuse_retired_locations(data)

    sources = {}
    for sid, sconf in (data.get("sources") or {}).items():
        sconf = sconf or {}
        sources[sid] = SourceConfig(
            enabled=bool(sconf.get("enabled", True)),
            tuning=dict(sconf.get("tuning") or {}),
            searches=list(sconf.get("searches") or []),
        )

    notify = dict(data.get("notify") or {})
    token = os.environ.get("SLUICE_TELEGRAM_TOKEN")
    chat = os.environ.get("SLUICE_TELEGRAM_CHAT")
    if token or chat:  # keep secrets out of the yaml; supply via env
        tele = dict(notify.get("telegram") or {})
        if token:
            tele["token"] = token
        if chat:
            tele["chat_id"] = chat
        notify["telegram"] = tele

    # Validate here so a malformed entry fails at CONSTRUCTION, naming the key and
    # the entry's index -- never its value. Deliberately NOT _str_list: that raises
    # with `got {value!r}`, i.e. the whole list, and a config file is one of the few
    # places a user's real private hostnames legitimately live.
    raw_allow = data.get("dossier_allow_hosts")
    # Pass the RAW value: `list(...)` first would explode a YAML scalar into one
    # entry per character BEFORE parse_allow_hosts' isinstance guard could fire, so
    # `dossier_allow_hosts: myboard` would load silently as seven inert one-character
    # grants on a SAFETY allowlist. That is the bug class `_str_list` exists for.
    parse_allow_hosts([] if raw_allow is None else raw_allow)
    allow = list(raw_allow or [])   # coerce only AFTER validation has passed

    # #9. ABSENT is the abstain case, not an error -- an unconfigured install must load
    # and expire nothing. `bool` is checked FIRST and separately because it SUBCLASSES
    # int: PyYAML resolves yes/on/true to True, so `lead_ttl_days: yes` -- what a user
    # naturally types to turn this feature ON -- would otherwise pass an isinstance(int)
    # check and set a ONE-DAY ttl, marking every lead stale with no error anywhere.
    raw_ttl = data.get("lead_ttl_days")
    raw_ttl = 0 if raw_ttl is None else raw_ttl
    if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, int) or raw_ttl < 0:
        raise ValueError(
            f"lead_ttl_days must be a non-negative integer (0 = off), got {raw_ttl!r}")

    # #1. Validated HERE as well as in `Vault.__init__`, and the two are NOT redundant. A
    # loader-only check is an equivalent mutant for every one of the ~150 direct `Vault(...)`
    # constructions in the suite; a constructor-only check lets a typo in the YAML reach the user
    # as an uncaught ValueError traceback out of `args.func`, where `lead_ttl_days` above renders
    # a `sluice: ...` usage error and exits 2. Same knob shape, same failure surface.
    raw_layout = data.get("lead_layout")
    if raw_layout is not None and raw_layout not in LEAD_LAYOUTS:
        raise ValueError(
            f"lead_layout must be one of "
            f"{', '.join(repr(n) for n in LEAD_LAYOUTS)}, got {raw_layout!r}")

    # NB this loader names every field EXPLICITLY -- no splat, no loop, unlike the four
    # sub-app loaders' hasattr+setattr loops. A dataclass field added without a line
    # here is therefore dead: it loads as its default whatever the YAML says, silently.
    #
    # That is NOT what happened to triage/config.py's two dead keys, which is a different
    # failure with the same symptom and worth keeping distinct: the triage LOADER read
    # them fine (its loop sets any field the dataclass declares), but nothing downstream
    # ever consulted the result -- app.py read $TRIAGE_AUDIT and $DOSSIER_DIR directly.
    # A key can therefore die at either end, and only enumerating BOTH finds them.
    return Config(sources=sources, notify=notify,
                  store=str(data.get("store") or "vault"),
                  baseline_rel=str(data.get("baseline_rel") or "My CV/CV.md"),
                  vault_dir=str(data.get("vault_dir") or ""),
                  dossier_dir=str(data.get("dossier_dir") or ""),
                  fetcher=str(data.get("fetcher") or "camofox"),
                  relevance_keep=list(data.get("relevance_keep") or []),
                  relevance_drop=list(data.get("relevance_drop") or []),
                  location_noise_words=_str_list(data.get("location_noise_words"),
                                                 "location_noise_words"),
                  dedupe_title_noise_words=_str_list(data.get("dedupe_title_noise_words"),
                                                     "dedupe_title_noise_words"),
                  lead_ttl_days=raw_ttl,
                  lead_layout=raw_layout or "",
                  dossier_allow_hosts=allow)
