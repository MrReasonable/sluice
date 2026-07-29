"""Apply configuration: code defaults overlaid by the `apply:` block of
sluice.yaml. Every field has a sane default so apply runs with no config file."""
import os
from dataclasses import dataclass

from sluice.core.paths import config_file

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class ApplyConfig:
    served_dir: str = "./cv-served"                   # where sluice cv serves PDFs
    camofox_upload_dir: str = "./cv-host"              # bind-mounted into Camofox (:ro)
    camofox_cv_dir: str = "./cv-uploads"               # the CV dir as seen INSIDE the browser container
    neutral_name: str = "CV.pdf"                      # what a recruiter sees on the form
    # Must match cv.config.CvConfig.served_prefix: the prefix render.serve() used
    # when naming the PDF this module resolves from served_dir.
    served_prefix: str = "CV"


def load_apply_config(path: str | None = None) -> ApplyConfig:
    cfg = ApplyConfig()
    path = path or config_file()
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = (yaml.safe_load(f) or {}).get("apply") or {}
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg
