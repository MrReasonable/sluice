"""CV configuration: code defaults overlaid by the `cv:` block of sluice.yaml.
Every field has a sane default so cv runs with no config file. Secrets via env."""
import os
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Employer-code and negative-fact-check lists are entirely personal to whoever
# runs sluice, so the code ships with no entries; supply your own via the `cv:`
# block of sluice.yaml (see sluice.yaml.example).
_PREFIX_MAP: dict = {}
_NEGATIVES: list = []


@dataclass
class CvConfig:
    name: str = "Your Name"
    # Contact block inserted verbatim into the composed CV (phone/email/web lines,
    # whatever format you want the renderer to see). Entirely personal, so the
    # code ships with no contact info; supply your own via the `cv:` block of
    # sluice.yaml (see sluice.yaml.example).
    contact: str = ""
    # Employers the composer must cite and the validate() gate must see present.
    # Empty by default: with no list configured, compose.py asks the model to
    # include every employer present in the source bundle instead of a fixed
    # list, and validate.py skips the per-employer completeness check.
    employers: list = field(default_factory=list)
    # Strings the validate() gate treats as known-hallucination decoys (a HARD
    # FAIL if any appear in the composed CV). Empty by default; supply your own
    # via the `cv:` block of sluice.yaml.
    fabrication_decoys: list = field(default_factory=list)
    # Prefix used for the served/staged PDF filename: "{served_prefix}_<sha1>.pdf".
    # Must match apply.config.ApplyConfig.served_prefix so apply/cvfile.py's
    # artifact regex recognizes files this module serves.
    served_prefix: str = "CV"
    prefix_map: dict = field(default_factory=lambda: dict(_PREFIX_MAP))
    negatives: list = field(default_factory=lambda: list(_NEGATIVES))
    baseline_rel: str = "My CV/CV.md"
    ttl_days: int = 7
    dossier_dir: str = "./dossiers"
    render_script: str = "./scripts/cv_render_v2.py"
    render_python: str = "/usr/bin/python3"
    render_home: str = "./cv-home"
    output_dir: str = "./cv-output"
    served_dir: str = "./cv-served"
    vault_cv_dir: str = "My CV/tailored"
    neutral_filename: str = "CV.pdf"
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    compose_model: str = "claude-sonnet-4-5"  # proven on the configured claude-max host's CLI (2.1.202); claude-sonnet-5 is NOT accepted there
    compose_effort: str = "max"
    cheap_model: str = "deepseek-v4-flash"
    audit_model: str = "claude-sonnet-4-5"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_path locally; set a host to shell out over ssh.
    compose_host: str = ""
    compose_claude_path: str = "claude"


def load_cv_config(path: str | None = None) -> CvConfig:
    cfg = CvConfig()
    path = path or os.environ.get("SLUICE_CONFIG")
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = (yaml.safe_load(f) or {}).get("cv") or {}
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg
