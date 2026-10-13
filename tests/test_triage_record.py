"""#196: evidence-aware triage -- the candidate record the judge may be shown."""
import ast
from pathlib import Path

import pytest

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


def test_judge_fields_registry():
    from sluice.core.protocols import EVIDENCE_KINDS
    assert EVIDENCE_KINDS["experience"].judge_fields == (
        "Company", "Category", "Best For", "Metrics", "Tools")
    assert EVIDENCE_KINDS["skills"].judge_fields == ("Proficiency", "Domain")
    assert EVIDENCE_KINDS["stories"].judge_fields == ()


def test_experience_skills_never_reach_the_judge_beside_a_company():
    """By the owner's model an entry's `Skills:` are general skills tied to NO job, so a
    line printing them beside `Company:` would attribute them to that employer."""
    from sluice.core.protocols import EVIDENCE_KINDS
    assert "Skills" not in EVIDENCE_KINDS["experience"].judge_fields


def test_judge_fields_must_be_declared_fields():
    from sluice.core.protocols import EvidenceKind
    with pytest.raises(ValueError, match="judge_fields"):
        EvidenceKind("Job Applications/X", ("Domain",), judge_fields=("Domian",))


def test_doctor_names_triage_evidence_for_a_judge_only_kind(monkeypatch):
    """`core/doctor.py` binds EVIDENCE_KINDS at import, so THAT binding is the one patched.
    The stories row is asserted EQUAL to its pre-change text, not by substring: a substring
    check passes if the judge wording leaked onto every unread kind."""
    from sluice.core import doctor
    from sluice.core.protocols import EVIDENCE_KINDS, EvidenceKind
    judge_only = EvidenceKind("Job Applications/Judge Only", ("Domain",),
                              judge_fields=("Domain",))
    kinds = {**EVIDENCE_KINDS, "judgeonly": judge_only}
    monkeypatch.setattr(doctor, "EVIDENCE_KINDS", kinds)
    facts = {"vault_exists": True, "criteria_present": True}
    for k in kinds:
        facts.update({f"{k}_verified": 1, f"{k}_total": 1, f"{k}_pending": 0})
    rows = {r.subject: r.detail for r in doctor.classify_store(facts)}
    suffix = "; read by the triage judge when triage.evidence is on"
    assert rows["Judge Only"] == f"1 verified / 1 total entries -- reviewed{suffix}"
    assert rows["STAR Stories"] == ("1 verified / 1 total entries -- reviewed, but nothing "
                                    "reads this corpus yet")
    # The REAL judge-read kinds are also read by the composer, so an earlier arm writes
    # their base text: the judge clause must still end each of their rows.
    judged = {spec.relpath.rsplit("/", 1)[-1]
              for spec in EVIDENCE_KINDS.values() if spec.judge_fields}
    assert judged == {"Experience Library", "Skills Inventory"}
    for subject in judged:
        assert rows[subject].endswith(suffix), subject
