"""Strip citations, render the neutral-filename PDF via cv_render_v2.py (shelled out;
HOME-env dependent), and serve it under an opaque cache-busting name. The subprocess
runner is injected for tests."""
import hashlib
import os
import re
import shutil
import subprocess

_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")


def strip_citations(cv_text: str) -> str:
    return _CITE_RE.sub("", cv_text)


def render(cv_text, out_dir, *, render_script, python_bin="/usr/bin/python3",
           home="./cv-home", neutral_name="CV.pdf",
           runner=subprocess.run) -> str:
    os.makedirs(out_dir, exist_ok=True)
    clean_path = os.path.join(out_dir, "cv_clean.txt")
    pdf_path = os.path.join(out_dir, neutral_name)
    with open(clean_path, "w", encoding="utf-8") as f:
        f.write(strip_citations(cv_text))
    env = {**os.environ, "HOME": home}
    proc = runner([python_bin, render_script, clean_path, pdf_path],
                  capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"render failed: {proc.stderr[:200]}")
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"render exited 0 but wrote no file: {pdf_path}")
    return pdf_path


def serve(pdf_path, served_dir, served_prefix="CV") -> str:
    os.makedirs(served_dir, exist_ok=True)
    with open(pdf_path, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:8]
    name = f"{served_prefix}_{digest}.pdf"
    shutil.copyfile(pdf_path, os.path.join(served_dir, name))
    return name
