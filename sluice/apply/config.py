"""Apply configuration: code defaults overlaid by the `apply:` block of
sluice.yaml. Every field has a sane default so apply runs with no config file."""
import os
from dataclasses import dataclass

from sluice.core.config import refuse_wrong_container, sub_app_block
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
        data = sub_app_block("apply", (yaml.safe_load(f) or {}).get("apply"))
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            # #176. ApplyConfig has NO container field today, so this is currently
            # a no-op -- wired anyway because this loop is otherwise a bare
            # `hasattr`+`setattr` with no shape guard at all, and the field it
            # gains next would be silently unprotected. Guarding three loaders and
            # leaving the fourth bare is the half-applied defensive pattern this
            # repo treats as worse than none. `track` is deliberately NOT wired:
            # its two dict fields are already refused by `_merge_denylist`, whose
            # message is strictly more specific than this one.
            refuse_wrong_container("apply", k, v, getattr(cfg, k))
            setattr(cfg, k, v)
    return cfg
