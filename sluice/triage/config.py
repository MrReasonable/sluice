"""Triage configuration: code defaults overlaid by the `triage:` block of
sluice.yaml. Role/geo/pay rules live here so tuning your preferences is a
config edit, not a code change. Every field has a sane default, so triage runs
with no config file at all."""
import os
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Role preferences are entirely personal, so the code ships with NO opinion:
# which titles you want (and which disqualify a role) is yours to declare in the
# `triage:` block of sluice.yaml (see sluice.yaml.example). Empty accept/reject
# lists mean the title gate simply abstains and every lead reaches the LLM judge,
# which reads the criteria from your vault Judging Profile.
_ACCEPT: list = []
_REJECT: list = []
# Geo/company preferences are entirely personal, so the code ships with
# neutral, generic defaults; supply your own via the `triage:` block of
# sluice.yaml (see sluice.yaml.example).
_TARGET_LOC = ["remote"]
_REJECT_LOC: list = []
_REJECT_CO: list = []


@dataclass
class TriageConfig:
    accept_titles: list = field(default_factory=lambda: list(_ACCEPT))
    reject_titles: list = field(default_factory=lambda: list(_REJECT))
    target_locations: list = field(default_factory=lambda: list(_TARGET_LOC))
    reject_locations: list = field(default_factory=lambda: list(_REJECT_LOC))
    reject_companies: list = field(default_factory=lambda: list(_REJECT_CO))
    contract_floor_gbp_day: int = 0
    perm_floor_gbp: int = 0
    batch_size: int = 5
    ttl_days: int = 7
    dossier_dir: str = "./dossiers"
    audit_jsonl: str = "./triage-audit.jsonl"
    # A single rolling digest, named distinctly from the legacy per-lead
    # "Rejected Leads/" folder so the two do not collide in Obsidian.
    rejected_note: str = "Job Applications/Rejected Leads Audit.md"
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    cheap_model: str = "deepseek-v4-flash"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_effort: str = "medium"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_max_path locally; set a host to shell out over ssh.
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    route_borderline: bool = False


def load_triage_config(path: str | None = None) -> TriageConfig:
    cfg = TriageConfig()
    path = path or os.environ.get("SLUICE_CONFIG")
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = (yaml.safe_load(f) or {}).get("triage") or {}
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg
