"""The slash command for the career_interview prompt is `/mcp__<name>__career_interview`, where
<name> is what the USER registered the server as, not the name `MCPServer(...)` is built with.
Shipped code and prose therefore must not hard-code a prefix; the docs may show the documented
registration's form, but only beside the rule that the middle part is the registered name."""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXED = re.compile(r"/mcp__[A-Za-z0-9-]+__")
DOCS_FORM = "/mcp__job-sluice__career_interview"
RULE = re.compile(r"registered|registration|name you gave", re.I)


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def _text(rel):
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_no_fixed_prefix_in_shipped_code_or_playbooks():
    files = [p for p in _tracked() if p.startswith("sluice/")]
    assert any(p.endswith("coach/handoff.md") for p in files)  # scope: the playbooks were swept
    bad = [p for p in files if FIXED.search(_text(p))]
    assert bad == [], f"hard-coded /mcp__<server>__ prefix in {bad}; use the /mcp__<name>__ placeholder"


def test_docs_give_the_registered_form_only_beside_the_naming_rule():
    files = [p for p in _tracked() if p == "README.md" or p.startswith(("docs/", ".rulesync/"))]
    files = [p for p in files if not p.startswith("docs/superpowers/") and p.endswith(".md")]
    mentions = [p for p in files if FIXED.search(_text(p))]
    assert {"README.md", "docs/USAGE.md", "docs/MCP.md", "docs/CONFIGURATION.md"} <= set(mentions)
    for p in mentions:
        t = _text(p)
        assert all(m.group(0) == "/mcp__job-sluice__" for m in FIXED.finditer(t)), p
        assert DOCS_FORM in t and RULE.search(t), f"{p} shows the command without the naming rule"
