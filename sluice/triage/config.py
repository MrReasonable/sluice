"""Triage configuration: code defaults overlaid by the `triage:` block of
sluice.yaml. Role/geo/pay rules live here so tuning your preferences is a
config edit, not a code change. Every field has a sane default, so triage runs
with no config file at all."""
import os
from dataclasses import dataclass, field

from sluice.core.paths import config_file, resolve

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
# Geography and company preferences are personal too, so they ship empty as well.
# Empty target_locations means the location gate ABSTAINS (see classify): a fresh
# install must never silently reject every job that is not remote.
_TARGET_LOC: list = []
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
    # Blank, not a path (#80), and load_triage_config fills it in. A non-empty default
    # is always truthy, so it short-circuits `env or config key or XDG` before the XDG
    # location is reached -- the field would never move, silently.
    audit_jsonl: str = ""
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
    path = path or config_file()
    # An INVERTED guard rather than the early `return cfg` this replaced (#80): the
    # resolution below must run on every path out of this function, and the
    # no-config-file case is exactly what a fresh install gets.
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = (yaml.safe_load(f) or {}).get("triage") or {}
        for k, v in data.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
    # AFTER the loop, so `audit_jsonl: ""` in a config file resolves rather than
    # escaping as the empty string the loop just set.
    cfg.audit_jsonl = resolve(env_var="TRIAGE_AUDIT", config_value=cfg.audit_jsonl,
                              kind="state", name="triage-audit.jsonl")
    return cfg
