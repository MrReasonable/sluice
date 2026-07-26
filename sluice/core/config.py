"""Layered configuration: code defaults < sluice.yaml < environment.

Every knob has a sane default so sluice runs with no config file at all; a
sluice.yaml overrides pieces of it; env vars win last so ops and offline tests
can override without editing files.
"""
import os
from dataclasses import dataclass, field

from sluice.core.urlguard import parse_allow_hosts

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

# Geography is a personal preference, so the code ships with none. This was
# ["Remote"], which is the same shape as the bug 672ad2a fixed in triage: a geo
# preference baked into shipped source. It survived here because nothing reads
# `Config.locations` yet -- which made it a loaded gun rather than a live bug, since
# the first consumer to wire it into a search or a gate would have inherited a
# stranger's "remote only" and silently binned every located job.
_DEFAULT_LOCATIONS: list = []


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
    # Hosts/CIDRs exempt from the dossier fetcher's SSRF guard (#18). A SAFETY
    # allowlist, not a preference gate: empty means "no exceptions granted", NOT
    # "match nothing" -- an unconfigured install still fetches every public url,
    # because the address rule admits them, not this list. Lives on the root
    # Config, not TriageConfig/CvConfig, because dossier_cache is called from BOTH
    # sub-apps and a security policy that differs between them is a bug.
    dossier_allow_hosts: list = field(default_factory=list)
    locations: list = field(default_factory=lambda: list(_DEFAULT_LOCATIONS))
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


def load_config(path: str | None = None) -> Config:
    data = {}
    path = path or os.environ.get("SLUICE_CONFIG")
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    sources = {}
    for sid, sconf in (data.get("sources") or {}).items():
        sconf = sconf or {}
        sources[sid] = SourceConfig(
            enabled=bool(sconf.get("enabled", True)),
            tuning=dict(sconf.get("tuning") or {}),
            searches=list(sconf.get("searches") or []),
        )

    locations = list(data.get("locations") or _DEFAULT_LOCATIONS)
    env_loc = os.environ.get("SLUICE_LOCATIONS")
    if env_loc:  # env wins last: comma-separated
        locations = [s.strip() for s in env_loc.split(",") if s.strip()]

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

    return Config(sources=sources, locations=locations, notify=notify,
                  store=str(data.get("store") or "vault"),
                  baseline_rel=str(data.get("baseline_rel") or "My CV/CV.md"),
                  fetcher=str(data.get("fetcher") or "camofox"),
                  relevance_keep=list(data.get("relevance_keep") or []),
                  relevance_drop=list(data.get("relevance_drop") or []),
                  location_noise_words=_str_list(data.get("location_noise_words"),
                                                 "location_noise_words"),
                  dedupe_title_noise_words=_str_list(data.get("dedupe_title_noise_words"),
                                                     "dedupe_title_noise_words"),
                  lead_ttl_days=raw_ttl,
                  dossier_allow_hosts=allow)
