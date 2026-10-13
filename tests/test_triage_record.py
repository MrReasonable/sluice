"""#196: evidence-aware triage -- the candidate record the judge may be shown."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cv_name_lives_in_core_and_cv_selection_reuses_it():
    from sluice.core import protocols
    from sluice.cv import selection
    assert selection.cv_name is protocols.cv_name


def test_skill_label_and_cv_name():
    from sluice.core.protocols import cv_name, skill_label
    name = "Example Query"
    # Padded at runtime: the fixture-name collectors read literal values, and a padded
    # literal is not on the reviewed skill roster.
    assert skill_label({"fields": {"Label": f"  {name} "}}) == name
    assert skill_label({"fields": {"Label": ""}, "title": "s"}) == ""
    assert skill_label({}) == ""
    assert cv_name({"fields": {"Label": name}, "title": "alpha"}) == name
    assert cv_name({"fields": {}, "title": f" {'alpha'} "}) == "alpha"


def _resolved_imports(path, package):
    """Every module an import in `path` names, relative imports resolved against
    `package`, and `from sluice import cv` reported as `sluice.cv` -- the two spellings a
    `startswith("sluice.cv")` matcher misses (tests/test_core_layering.py replaced exactly
    that matcher for this reason)."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            out.append(base)
            out += [f"{base}.{a.name}" for a in node.names]
    return out


def test_triage_imports_nothing_from_cv():
    """SCOPE first: the sweep must have found triage modules, or it certifies nothing."""
    files = sorted((ROOT / "sluice" / "triage").glob("*.py"))
    assert len(files) >= 5, files
    offenders = [f"{f.name}: {m}" for f in files
                 for m in _resolved_imports(f, "sluice.triage")
                 if m == "sluice.cv" or m.startswith("sluice.cv.")]
    assert offenders == []


def test_the_import_matcher_sees_every_spelling(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("from sluice import cv\nfrom ..cv import selection\n"
                     "import sluice.cv.engine\n")
    mods = _resolved_imports(probe, "sluice.triage")
    assert {"sluice.cv", "sluice.cv.selection", "sluice.cv.engine"} <= set(mods)
