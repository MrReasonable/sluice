"""Triage configuration: code defaults overlaid by the `triage:` block of
sluice.yaml. Role/geo/pay rules live here so tuning your preferences is a
config edit, not a code change. Every field has a sane default, so triage runs
with no config file at all."""
import os
from dataclasses import dataclass, field

from sluice.core.config import (refuse_retired_dossier_dir,
                                refuse_wrong_container, sub_app_block)
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
    # NB no `dossier_dir` here: it was a DEAD key (declared, read by nothing) and is
    # retired outright by #80 in favour of one root `dossier_dir`. load_triage_config
    # RAISES on it rather than letting `hasattr` drop it in silence.
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
    # Off by default (#109): gates the tier-2 (real, no-LLM page-visit) half of
    # blank/placeholder-company resolution independently of --no-llm. An unconfigured install
    # must not start opening real browser tabs against arbitrary third-party sites
    # for its whole needs_review backlog the moment it upgrades -- the same
    # abstain-by-default posture as lead_ttl_days/lead_layout. Tier 1 (free,
    # URL-pattern-only) is unaffected by this knob and always runs.
    company_resolve_fetch: bool = False
    # Off by default (#120): gates tier 3, which hands the page data tier 2 already
    # fetched (no second page visit) to the CHEAP backend instead of two regexes.
    # A SIBLING of company_resolve_fetch, not a widening of it: the two buy different
    # things with different currencies -- the fetch spends a real page load, this
    # spends money -- so an install that already opted into the free-network page
    # visit must not silently start paying for LLM calls the moment it upgrades.
    # STRICTLY narrower than company_resolve_fetch; see load_triage_config's
    # cross-field check below.
    company_resolve_llm: bool = False


def load_triage_config(path: str | None = None) -> TriageConfig:
    cfg = TriageConfig()
    path = path or config_file()
    # An INVERTED guard rather than the early `return cfg` this replaced (#80): the
    # resolution below must run on every path out of this function, and the
    # no-config-file case is exactly what a fresh install gets.
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = sub_app_block("triage", (yaml.safe_load(f) or {}).get("triage"))
        refuse_retired_dossier_dir("triage", data)
        for k, v in data.items():
            if not hasattr(cfg, k) or v is None:
                continue
            # A field whose CODE DEFAULT is a bool must be given a real YAML boolean.
            # The mirror image of the root loader's lead_ttl_days check (core/config.py):
            # there the hazard is that PyYAML resolves `yes`/`on`/`true` to a real bool
            # which then passes an isinstance(int) test; here it is that a QUOTED
            # `company_resolve_fetch: "false"` is not a YAML boolean at all -- it stays
            # the string "false", which this loop would setattr verbatim and every
            # consumer reads in a boolean context, where a non-empty string is TRUE. So
            # the one spelling a user reaches for to keep a knob OFF is the spelling that
            # silently switches it ON, with nothing anywhere going red. Fail loudly at
            # construction instead, this file's house style.
            #
            # Keyed on the default's type rather than on a hardcoded field list so a bool
            # knob added later cannot quietly opt out of the check. `getattr(cfg, k)` is
            # still the code default here: a YAML mapping yields each key once, so no
            # earlier iteration of this loop has replaced it.
            if isinstance(getattr(cfg, k), bool) and not isinstance(v, bool):
                raise ValueError(
                    f"triage.{k} must be a YAML boolean (true/false), got {v!r}. Quoted, "
                    f'it is a STRING -- and "false" is truthy in Python, so the knob '
                    f"would be switched ON by the value meant to switch it off.")
            # #176, the container sibling of the bool guard above and keyed the same
            # way. These five fields are the PREFERENCE GATES: measured before this
            # existed, `target_locations: remote` loaded as a str and `classify` then
            # kept every location, byte-identical to the unconfigured abstain -- a
            # geography filter the user believes they configured doing nothing at all.
            refuse_wrong_container("triage", k, v, getattr(cfg, k))
            setattr(cfg, k, v)
    # #120: unconditional (not inside the `if path...` block above) because it must
    # run on every LOAD, whether or not a config FILE was present -- the DEFAULT
    # state (both False) has to pass trivially, and a file that sets ONLY
    # company_resolve_llm (leaving company_resolve_fetch at its own False default)
    # must still be caught. Placed after the overlay loop because it needs both
    # keys' FINAL values, and PyYAML yields a mapping's keys in file order -- a
    # check placed inside the loop would pass or fail depending on which key
    # happened to come first in the file.
    #
    # This is not what makes tier 3 SAFE -- resolve.py's tier-3 block sits after the
    # existing `if no_llm or not company_resolve_fetch or not url: return` early
    # exit, so it structurally cannot fire without the fetch regardless of this
    # check. It exists because a config that claims a feature is on while it can
    # never run is the same "declared and read by nothing" class
    # refuse_retired_dossier_dir already guards against for a retired key -- fail
    # loudly at construction, this file's house style.
    if cfg.company_resolve_llm and not cfg.company_resolve_fetch:
        raise ValueError(
            "triage.company_resolve_llm is on but triage.company_resolve_fetch is off. "
            "Tier 3 reads the page data tier 2 fetches, so on its own it can never "
            "fire: the knob would be silently inert, every blank/placeholder-company lead would "
            "stay unresolved, and the config would say otherwise. Set "
            "company_resolve_fetch: true as well, or turn company_resolve_llm off.")
    # AFTER the loop, so `audit_jsonl: ""` in a config file resolves rather than
    # escaping as the empty string the loop just set.
    cfg.audit_jsonl = resolve(env_var="TRIAGE_AUDIT", config_value=cfg.audit_jsonl,
                              kind="state", name="triage-audit.jsonl")
    return cfg
