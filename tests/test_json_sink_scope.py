"""No `ensure_ascii=False` call site may be on a print path (#280).

The design's prose roster of these sites was written by hand twice and was wrong in BOTH
directions both times -- it named a site that is at the default and omitted one whose call is
`json.dump` rather than `json.dumps`, which the roster's own grep could not match. This sweep
is what makes the claim safe to rely on.
"""
import ast
import pathlib

# Hand-written TARGET. The probe alphabet below is derived by walking the AST; this value is
# not, deliberately -- a target derived from the same matcher cannot see a deletion.
EXPECTED_SINKS = {
    # (path, INNERMOST enclosing function) -> what it writes to, and why that is not a terminal.
    # Derived once by running the sweep, then hand-written here. Note `core/vault.py`'s site sits
    # in a nested `transform`, which is why attribution must be to the innermost function: walking
    # every enclosing FunctionDef reports one site twice and the target silently stops matching.
    ("sluice/core/dossier.py", "get_or_build"): "the dossier cache file",
    ("sluice/core/vault.py", "transform"): "note frontmatter",
    ("sluice/triage/audit.py", "append"): "the audit JSONL file",
    ("sluice/triage/judge.py", "_build_prompt"): "the judge prompt",
}


def _sites():
    """Every `ensure_ascii=False` call site, keyed by its INNERMOST enclosing function."""
    found = set()
    for path in sorted(pathlib.Path("sluice").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node.name
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg == "ensure_ascii" and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False):
                        found.add((str(path), fn))
            for child in ast.iter_child_nodes(node):
                visit(child, fn)

        visit(tree, "<module>")
    return found


def test_the_sweep_found_the_sites_it_meant_to():
    """Anti-vacuity: a broken walk enumerates nothing and every assertion over it passes."""
    assert len(_sites()) == len(EXPECTED_SINKS), (
        "the sweep enumerated a different number of sites than the hand-written target -- "
        "check the walk before editing the target")


def test_no_ensure_ascii_false_site_is_on_a_print_path():
    assert _sites() == set(EXPECTED_SINKS), (
        "an ensure_ascii=False call site was added or moved. If it writes to a FILE or a PROMPT, "
        "add it to EXPECTED_SINKS with a note saying which. If it can reach a stream, it must "
        "not use ensure_ascii=False -- see sluice/apply/packet.py::render_json.")
