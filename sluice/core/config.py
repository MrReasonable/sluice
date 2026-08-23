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
    # The floor below which a fetched JD is treated as NOT HAVING ARRIVED (#169), so the
    # dossier cache refuses to persist it and triage refuses to spend a judge call on it.
    # 0 = the band is OFF and is the SHIPPED default: an EMPTY jd always fails (a fact),
    # but a character count is a judgement about what counts as a real posting, and an
    # active value would hand every copier one they never made -- the same rule
    # sluice.yaml.example states at length for `lead_ttl_days`. `job-sluice init` asks.
    # ROOT, not per-sub-app, because triage and cv SHARE one dossier directory: two
    # different floors over one directory means whichever sub-app ran last decides
    # whether an entry exists.
    min_jd_chars: int = 0

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
        # Names the TYPE, never the VALUE. `relevance_keep`/`relevance_drop` are job-title
        # preferences and `location_noise_words` is geography -- personal, and a config
        # file is one of the few places a user's real ones legitimately live. That is the
        # same reasoning `load_config` gives for routing `dossier_allow_hosts` through
        # `parse_allow_hosts` INSTEAD of this helper, and `urlguard.py` already prints
        # `type(entries).__name__` for it; echoing here made that choice inconsistent with
        # itself (#176).
        raise ValueError(
            f"{name} must be a YAML list of strings, but got a {type(value).__name__}. "
            f"Write it as `{name}: [first, second]`, or one `- first` per line.")
    return list(value)


def refuse_wrong_container(block: str, key: str, value, default) -> None:
    """Refuse a YAML SCALAR given for a field whose CODE DEFAULT is a list or a dict.

    Keyed on the DEFAULT's type, never on a field list, so a container field added
    later cannot quietly opt out -- the same shape, and the same reasoning, as the
    quoted-bool guard this sits beside. Shape only, never range: `compose_timeout > 0`
    and the cross-field checks stay where their own reasons live.

    #176. `_str_list` below already named this exact hazard ("would otherwise
    `list()`-explode into single characters and silently mis-configure the gate") and
    was then wired to two root fields and none of the sub-app ones -- the half-applied
    defensive pattern this codebase treats as worse than none. Measured on the pre-fix
    tree, all three shapes are 672ad2a reached through a YAML typo instead of a shipped
    default:

      * `relevance_drop: senior` loaded as `['s','e','n','i','o','r']`, and
        `is_relevant` then returned False for EVERY title -- the whole scrape binned at
        ingest, before dedup and before a note exists anywhere to notice.
      * `triage.target_locations: remote` loaded as a `str`, and `classify` then kept
        every location: byte-identical to the unconfigured abstain, so a filter the
        user believes they configured does nothing.
      * `cv.fabrication_decoys: Acme` made the CV gate emit `FABRICATED: contains 'A'`
        and hard-block every CV.

    RAISES rather than coercing, and that is the load-bearing choice. Coercion looks
    kinder -- `remote` clearly means `[remote]` -- but it cannot be made safe, because
    the likeliest scalar is the COMMA-SEPARATED one: `job-sluice init` asks for these
    answers "comma-separated", and a user hand-editing YAML repeats that phrasing.
    `target_locations: London, Berlin` coerces to ONE token matching nothing, so every
    located lead is rejected. Coercion converts "the gate abstains" into "the gate
    matches nothing" -- the same bug class, one step further from view. Raising is also
    this repo's unanimous house style: every validator here refuses and names the key,
    and nothing coerces an unvalidated value.

    Never echoes the VALUE. `reject_companies`, `target_locations` and `employers` hold
    a real person's preferences, and `load_config` already declines `_str_list` for
    `dossier_allow_hosts` on exactly this ground.
    """
    if isinstance(default, bool) or not isinstance(default, (list, dict)):
        return
    if isinstance(value, type(default)):
        return
    qualified = f"{block}.{key}" if block else key
    if isinstance(default, list):
        shape = (f"Write it as `{key}: [first, second]`, or one `- first` per line. "
                 f"A bare `{key}: value` is a STRING, and sluice would read it one "
                 f"CHARACTER at a time.")
    else:
        shape = (f"Write it as `{key}:` followed by indented `name: value` lines.")
    raise ValueError(
        f"{qualified} must be a YAML {'list' if isinstance(default, list) else 'mapping'}, "
        f"but got a {type(value).__name__}. {shape}")


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
    # #176: a scalar here reached `.items()` and died with a bare AttributeError
    # naming no key at all -- worse than the list case, which at least produced a
    # value. The per-source check below is the same hazard one level down.
    if data.get("sources") is not None:
        refuse_wrong_container("", "sources", data["sources"], {})
    for sid, sconf in (data.get("sources") or {}).items():
        if sconf is not None:
            refuse_wrong_container("sources", sid, sconf, {})
        sconf = sconf or {}
        if sconf.get("searches") is not None:
            refuse_wrong_container(f"sources.{sid}", "searches",
                                   sconf["searches"], [])
        if sconf.get("tuning") is not None:
            refuse_wrong_container(f"sources.{sid}", "tuning", sconf["tuning"], {})
        sources[sid] = SourceConfig(
            enabled=bool(sconf.get("enabled", True)),
            tuning=dict(sconf.get("tuning") or {}),   # guarded by the sid check above
            # #176: same hazard one level down -- a scalar would explode into one
            # search per character and quietly scrape nothing useful. Deliberately
            # NOT `_str_list`: a search is a `[label, url, {params}?]` TRIPLE, not a
            # string, so that helper's element check would reject every valid value.
            # Shape only is exactly the distinction `refuse_wrong_container` draws.
            searches=list(sconf.get("searches") or []),
        )

    if data.get("notify") is not None:
        refuse_wrong_container("", "notify", data["notify"], {})
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

    # #169. Same shape as lead_ttl_days above, same reason: bool checked FIRST and
    # separately because it SUBCLASSES int, so `min_jd_chars: yes` -- the natural
    # spelling to turn this on -- would otherwise pass isinstance(int) and load as a
    # one-character floor, letting nearly every fetched JD through with no error.
    raw_floor = data.get("min_jd_chars")
    raw_floor = 0 if raw_floor is None else raw_floor
    if isinstance(raw_floor, bool) or not isinstance(raw_floor, int) or raw_floor < 0:
        raise ValueError(
            f"min_jd_chars must be a non-negative integer (0 = off), got {raw_floor!r}")

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
                  # #176: `_str_list`, not `list(...)`. The bare `list()` here was the
                  # SEVEREST instance of the bug this helper was written for and the
                  # one the issue did not name -- these two run at INGEST, before
                  # dedup and before any LLM call, so `relevance_drop: senior` loaded
                  # as six single characters and binned the entire scrape with nothing
                  # written to the vault to notice it.
                  relevance_keep=_str_list(data.get("relevance_keep"),
                                           "relevance_keep"),
                  relevance_drop=_str_list(data.get("relevance_drop"),
                                           "relevance_drop"),
                  location_noise_words=_str_list(data.get("location_noise_words"),
                                                 "location_noise_words"),
                  dedupe_title_noise_words=_str_list(data.get("dedupe_title_noise_words"),
                                                     "dedupe_title_noise_words"),
                  lead_ttl_days=raw_ttl,
                  lead_layout=raw_layout or "",
                  min_jd_chars=raw_floor,
                  dossier_allow_hosts=allow)
