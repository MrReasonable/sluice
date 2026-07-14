"""Layered configuration: code defaults < sluice.yaml < environment.

Every knob has a sane default so sluice runs with no config file at all; a
sluice.yaml overrides pieces of it; env vars win last so ops and offline tests
can override without editing files.
"""
import os
from dataclasses import dataclass, field

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
    locations: list = field(default_factory=lambda: list(_DEFAULT_LOCATIONS))
    notify: dict = field(default_factory=dict)
    # Coarse ingest title filter. Personal, so empty by default: an unconfigured
    # gate passes everything through rather than applying someone else's taste.
    relevance_keep: list = field(default_factory=list)
    relevance_drop: list = field(default_factory=list)

    def source(self, id: str) -> SourceConfig:
        """Config for a source id; unlisted sources default to enabled + no tuning."""
        return self.sources.get(id, SourceConfig())


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

    return Config(sources=sources, locations=locations, notify=notify,
                  relevance_keep=list(data.get("relevance_keep") or []),
                  relevance_drop=list(data.get("relevance_drop") or []))
