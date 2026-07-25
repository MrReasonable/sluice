"""Track configuration: code defaults overlaid by the `track:` block of sluice.yaml.
Every field has a sane default so track runs with no config file."""
import os
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_ATS_RELAY_DOMAINS = {
    "greenhouse.io": "greenhouse", "ashbyhq.com": "ashby", "lever.co": "lever",
    "workable.com": "workable", "icims.com": "icims", "teamtailor.com": "teamtailor",
    "myworkday.com": "workday", "smartrecruiters.com": "smartrecruiters",
}


@dataclass
class TrackConfig:
    token_path: str = "./google_token.json"
    seen_db: str = "./track-seen.db"
    gmail_lookback_days: int = 2
    gmail_extra_query: str = ""
    calendar_lookahead_days: int = 45
    calendar_match_minutes: int = 30          # start-proximity window for dedup
    # Which backend fills each role. Track had no selectors while its backend was
    # hardcoded; it needs them now that construction is config-driven, and matches
    # the triage/cv defaults.
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_effort: str = "medium"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_max_path locally; set a host to shell out over ssh.
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"
    auto_status_min: float = 0.75             # min confidence to auto-advance a scheduling/offer signal
    auto_reject_min: float = 0.9              # stricter bar to auto-reject (F4)
    auto_apply_min: float = 0.75              # min receipt-classification confidence to auto-advance shortlist->applied on a domain-PROOF match
    ats_relay_domains: dict = field(default_factory=lambda: dict(_ATS_RELAY_DOMAINS))


def load_track_config(path: str | None = None) -> TrackConfig:
    cfg = TrackConfig()
    path = path or os.environ.get("SLUICE_CONFIG")
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = (yaml.safe_load(f) or {}).get("track") or {}
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg
