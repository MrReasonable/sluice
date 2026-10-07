"""Model-researched text must not reach a scoring or composing decision: the Role Brief is
read only by in-session setup and the coach. A sweep over sluice/ plus a behavioural sentinel."""
import ast
from pathlib import Path

from sluice.core.protocols import ROLE_BRIEF_RELPATH

ROOT = Path(__file__).resolve().parent.parent / "sluice"
# (relative file, enclosing function or None for module level) where the note may be named.
_ALLOWED = {("core/protocols.py", None), ("core/app.py", "setup_snapshot"),
            ("core/app.py", "apply_setup"), ("mcpserver.py", "setup_status"),
            ("mcpserver.py", "setup_review_step")}
# Where only the NAME may appear, in prose a client reads: setup_review's registered
# description lives inside build_server. A read or the constant there would still fail.
_ALLOWED_LITERAL = {("mcpserver.py", "build_server")}
_ALLOWED_FILES = {"onboard/review.py"}
_ALLOWED_DIRS = ("onboard/coach/",)
_DOC_CONSTANTS = {"CRITERIA_RELPATH", "CANDIDATE_PROFILE_RELPATH", "CV_LAYOUT_RELPATH",
                  "LEADS_VIEW_RELPATH"}


def _refs(path):
    tree = ast.parse(path.read_text())
    # SETUP_NOTES maps "brief" to the same path, so naming it is naming the note.
    carriers = {"ROLE_BRIEF_RELPATH", "SETUP_NOTES"}
    aliases = set(carriers)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {a.asname for a in node.names if a.name in carriers and a.asname}
    out = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            f = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Name) and child.id in aliases:
                out.append((f, "constant"))
            elif isinstance(child, ast.Attribute) and child.attr in carriers:
                # `import sluice.core.protocols as pr; pr.ROLE_BRIEF_RELPATH` is an Attribute,
                # invisible to a Name-only match.
                out.append((f, "constant"))
            elif isinstance(child, ast.Constant) and isinstance(child.value, str) and (
                    ROLE_BRIEF_RELPATH in child.value or "Role Brief" in child.value):
                out.append((f, "literal"))
            elif isinstance(child, ast.Call) and getattr(child.func, "attr", "") == "read_document":
                arg = child.args[0] if child.args else None
                if not (isinstance(arg, ast.Name) and arg.id in _DOC_CONSTANTS):
                    out.append((f, "read_document"))
            visit(child, f)
    visit(tree, None)
    return out


def test_only_setup_code_names_or_reads_the_role_brief():
    seen, bad = set(), []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWED_FILES or rel.startswith(_ALLOWED_DIRS):
            continue
        for func, kind in _refs(path):
            if (rel, func) in _ALLOWED or (kind == "literal" and (rel, func) in _ALLOWED_LITERAL):
                seen.add((rel, func))
            else:
                bad.append(f"{rel}:{func}:{kind}")
    assert not bad, bad
    # Scope: a sweep that enumerated nothing would pass the line above.
    assert ("core/protocols.py", None) in seen and ("core/app.py", "setup_snapshot") in seen


def test_the_sweep_catches_a_planted_reader(tmp_path):
    planted = tmp_path / "x.py"
    planted.write_text("def judge(store):\n    return store.read_document("
                       "'Job Applications/' + 'Role Brief.md')\n")
    assert sorted(k for _, k in _refs(planted)) == ["literal", "read_document"]


def test_a_marker_in_the_role_brief_reaches_neither_judge_nor_composer(tmp_path, monkeypatch):
    from sluice.core.protocols import CRITERIA_RELPATH
    from sluice.ingest import sources as _sources
    from tests.harness import PASSING_REPLY, ScriptedBackend, build_harness
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/harness",
                      rows=[{"title": "Example Title", "company": "Example Foundry",
                             "link": "https://remoteok.example/jobs/1", "salary": ""}])
    h.vault.write_document(ROLE_BRIEF_RELPATH, "# Role Brief\n\nMARKER-ROLE-BRIEF-7731\n")
    h.vault.write_document(CRITERIA_RELPATH, "## Who this candidate is\n\nMARKER-PROFILE-7731\n")
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_REPLY},
                              default_verdict="shortlist")
    app = h.sluice(backend)
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))
    app.compose_cv(all_shortlist=True)
    prompts = "\n".join(backend.prompts)
    assert "MARKER-PROFILE-7731" in prompts           # the channel is live (positive control)
    assert any(p.startswith("Compose a tailored CV for") for p in backend.prompts)
    assert "MARKER-ROLE-BRIEF-7731" not in prompts


def _kinds(tmp_path, src):
    planted = tmp_path / "x.py"
    planted.write_text(src)
    return sorted(k for _, k in _refs(planted))


def test_the_sweep_catches_every_reference_shape(tmp_path):
    assert _kinds(tmp_path, "from sluice.core.protocols import ROLE_BRIEF_RELPATH as p\n"
                            "def f():\n    return p\n") == ["constant"]
    assert _kinds(tmp_path, "import sluice.core.protocols as pr\n"
                            "def f():\n    return pr.ROLE_BRIEF_RELPATH\n") == ["constant"]
    assert _kinds(tmp_path, "from sluice.core.protocols import SETUP_NOTES\n"
                            "def f():\n    return SETUP_NOTES['brief']\n") == ["constant"]
    assert _kinds(tmp_path, "from sluice.core.protocols import SETUP_NOTES as N\n"
                            "def f():\n    return N['brief']\n") == ["constant"]
    assert _kinds(tmp_path, "import sluice.core.protocols as pr\n"
                            "def f():\n    return pr.SETUP_NOTES\n") == ["constant"]
