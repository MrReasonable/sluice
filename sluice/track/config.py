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


def _merge_denylist(key: str, value):
    """Validate a denylist override, then merge it OVER the shipped default.

    The `isinstance(v, dict)` test this replaces was a silent fall-through: a non-mapping
    override skipped the merge and took the plain-setattr branch instead, REPLACING the
    denylist. `ats_relay_domains: []` therefore emptied a safety list outright and
    `ats_relay_domains: 'oops'` turned it into a four-"entry" string whose keys are the
    characters o/p/s. Both WIDEN the proof tier -- a host that no longer reads as
    multi-tenant can prove which employer a receipt concerns -- which is the exact failure
    direction the merge exists to close, and the same class of bug (a user override
    replacing a shipped default) this branch already fixed once.

    So: fail loudly at construction, naming the key and the valid shape, rather than
    retaining-or-emptying either way. Retaining silently would be no better -- the user's
    entry would vanish and their in-house ATS would go unrecognised with no signal.

    Only the KEYS are load-bearing: `receipt._suffix_match` iterates them and never reads
    a value (labels are documentation for whoever opens the config). A non-str key is
    therefore not merely untidy -- it raises TypeError out of `host.endswith("." + k)` at
    match time, inside engine.run's per-message `except`, which swallows it WITHOUT
    adding the id to `seen`: a permanently re-failing poison message. Values stay
    unconstrained on purpose; nothing reads them, so rejecting a non-string label would
    be a gratuitous refusal."""
    valid = f"{key}:\n    host.example.invalid: your-label"
    if not isinstance(value, dict):
        raise ValueError(f"track.{key} must be a mapping of host -> label, "
                         f"got {type(value).__name__}. Valid form:\n  {valid}")
    for k in value:
        # Report only the offending KEY, never the whole user block: a config file is the
        # one place a user's real job-hunt hosts legitimately live, and an exception
        # message travels further (logs, bug reports) than the file does.
        if not (isinstance(k, str) and k.strip()):
            raise ValueError(f"track.{key} keys must be non-empty host strings, "
                             f"got {k!r} ({type(k).__name__}). Valid form:\n  {valid}")
    # User entries win on a key collision (relabelling a shipped domain is fine), but no
    # user block can DROP a shipped safety entry.
    return {**_MERGED_DENYLISTS[key], **value}


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
        if k in _MERGED_DENYLISTS:
            # Merge, never replace -- see _MERGED_DENYLISTS. No `isinstance` test guarding
            # the branch: a non-mapping value must RAISE, not fall through to the plain
            # setattr below, which would replace the safety denylist with whatever the
            # user wrote (see _merge_denylist).
            setattr(cfg, k, _merge_denylist(k, v))
        else:
            setattr(cfg, k, v)
    return cfg
