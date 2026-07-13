"""Single-lead CV attachment staging. Resolve a lead's sluice-cv artifact and
copy it into the Camofox upload dir under the neutral filename a recruiter sees.
Only ONE neutral file exists at a time (overwritten per lead), so this runs for
`prep --lead`, never for the `--all-shortlist` preview."""
import os
import re
import shutil

# sluice-cv serves PDFs as {served_prefix}_<sha1-hex>.pdf; tailored_cv stores that
# basename optionally followed by " (YYYY-MM-DD)". served_prefix must match
# cv.config.CvConfig.served_prefix (see ApplyConfig.served_prefix).


class CvFileError(Exception):
    pass


def _artifact_re(served_prefix):
    return re.compile(rf"^({re.escape(served_prefix)}_[0-9a-f]+\.pdf)(?:\s*\(.*\))?$")


def parse_artifact(value, served_prefix="CV"):
    """Return the sluice-cv PDF basename if `value` is the sluice-cv form, else
    None. Legacy `My CV/CV_*.pdf`, `SKIPPED ...` markers (any separator), and
    empty values return None."""
    if not value:
        return None
    v = value.strip().strip('"').strip()
    if v.upper().startswith("SKIPPED"):
        return None
    m = _artifact_re(served_prefix).match(v)
    return m.group(1) if m else None


def resolve_source(basename, cfg):
    return os.path.join(cfg.served_dir, basename)


def _is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5).startswith(b"%PDF")
    except OSError:
        return False


def stage(value, cfg):
    """Parse -> resolve -> verify source -> copy to neutral name -> verify dest.
    Returns the destination path. Raises CvFileError on any failure."""
    basename = parse_artifact(value, getattr(cfg, "served_prefix", "CV"))
    if basename is None:
        raise CvFileError(f"not a sluice-cv artifact: {value!r}")
    src = resolve_source(basename, cfg)
    if not (os.path.isfile(src) and os.path.getsize(src) > 0 and _is_pdf(src)):
        raise CvFileError(f"source CV missing or not a PDF: {src}")
    try:
        os.makedirs(cfg.camofox_upload_dir, exist_ok=True)
        dest = os.path.join(cfg.camofox_upload_dir, cfg.neutral_name)
        shutil.copyfile(src, dest)
    except OSError as e:
        raise CvFileError(f"failed to stage CV to {cfg.camofox_upload_dir}: {e}") from e
    if not (os.path.isfile(dest) and os.path.getsize(dest) > 0 and _is_pdf(dest)):
        raise CvFileError(f"staged CV failed verification: {dest}")
    return dest
