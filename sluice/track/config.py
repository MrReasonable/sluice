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

# The job boards sluice itself scrapes, keyed by their REGISTRABLE domain and valued by
# the `sluice/ingest/sources/` plugin id. Together with _ATS_RELAY_DOMAINS these are the
# hosts shared by MANY employers, which `track/receipt.py` refuses to read as proof of
# any one of them: a lead's `url` is the URL it was INGESTED from, so for a board-sourced
# lead that host is the board, not the employer. Registrable form on purpose -- the
# matcher suffix-matches, so "indeed.com" covers "uk.indeed.com" and
# "welcometothejungle.com" covers "app.welcometothejungle.com", and one key per board
# survives a source switching subdomains. google.com is here deliberately: sluice's
# google source ingests Google-jobs aggregator links, and google.com is about as far
# from employer-identifying as a host gets.
_JOB_BOARD_DOMAINS = {
    "80000hours.org": "eighty_k", "b-work.io": "bwork", "bayt.com": "bayt",
    "cord.com": "cord", "cwjobs.co.uk": "cwjobs", "escapethecity.org": "escape_city",
    "google.com": "google", "gulftalent.com": "gulftalent", "hackajob.co": "hackajob",
    "hired.com": "hired", "indeed.com": "indeed", "jobserve.com": "jobserve",
    "linkedin.com": "linkedin", "naukrigulf.com": "naukrigulf", "reed.co.uk": "reed",
    "remoteok.com": "remoteok", "theorg.com": "theorg", "totaljobs.com": "totaljobs",
    "wellfound.com": "wellfound", "welcometothejungle.com": "wttj",
    "weworkremotely.com": "weworkremotely", "workinstartups.com": "workinstartups",
}

# Config keys whose shipped default is a SAFETY DENYLIST: a user block MERGES over the
# default instead of replacing it. Replacing is the wrong direction of failure here --
# adding one in-house ATS would silently drop the other eight shipped entries and make
# the proof tier MORE permissive, the opposite of what the person adding a relay wants
# (and the opposite of what sluice.yaml.example's "add your own" promises). Deliberately
# scoped to these two dict keys: every scalar knob keeps plain last-wins overlay.
_MERGED_DENYLISTS = {"ats_relay_domains": _ATS_RELAY_DOMAINS,
                     "job_board_domains": _JOB_BOARD_DOMAINS}


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
    job_board_domains: dict = field(default_factory=lambda: dict(_JOB_BOARD_DOMAINS))


def load_track_config(path: str | None = None) -> TrackConfig:
    cfg = TrackConfig()
    path = path or os.environ.get("SLUICE_CONFIG")
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = (yaml.safe_load(f) or {}).get("track") or {}
    for k, v in data.items():
        if not (hasattr(cfg, k) and v is not None):
            continue
        if k in _MERGED_DENYLISTS and isinstance(v, dict):
            # Merge, never replace -- see _MERGED_DENYLISTS. User entries win on a key
            # collision (relabelling a shipped domain is fine), but no user block can
            # DROP a shipped safety entry, which is the only failure direction that
            # widens the proof tier.
            setattr(cfg, k, {**_MERGED_DENYLISTS[k], **v})
        else:
            setattr(cfg, k, v)
    return cfg
