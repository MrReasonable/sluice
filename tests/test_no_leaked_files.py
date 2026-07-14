"""No generated artefact or personal-data file may EVER be tracked in this repo.

This is a CI gate, not a rule, because the rule already existed and did not work.

.memsearch/ has been committed to this public repository THREE times. Each time the
.gitignore rule was present and correct. The rule was irrelevant: `.gitignore` only applies
to files git is not ALREADY TRACKING. The file became tracked via a `git stash -u` taken on
a branch whose .gitignore predated the rule; popping that stash restored it *already
staged*, and from then on every `git add -A` carried it forward invisibly.

So the defence cannot be a pattern in a file. It has to be an assertion that fails the
build, on every PR, whatever route the file took into the index.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent

# Generated from .rulesync/ (rulesync generate), or runtime artefacts of local tooling.
# None of them belong in git; some of them carry absolute home paths and session ids.
FORBIDDEN_PREFIXES = (
    ".memsearch/",   # MemSearch session transcripts: absolute home paths, session UUIDs
    ".claude/",      # generated from .rulesync/
    "CLAUDE.md",     # generated from .rulesync/rules/CLAUDE.md
    "AGENTS.md",
    "GEMINI.md",
    ".mcp.json",
    ".cursor/",
)


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                         timeout=30)
    return out.stdout.splitlines()


def test_no_generated_or_personal_artefact_is_tracked():
    tracked = _tracked_files()
    leaked = [f for f in tracked if any(f.startswith(p) for p in FORBIDDEN_PREFIXES)]
    assert not leaked, (
        f"{len(leaked)} generated/personal file(s) are TRACKED in a public repo: {leaked}\n"
        f"gitignore does not help once a file is tracked. Remove it from the index:\n"
        f"    git rm -r --cached {' '.join(leaked)}"
    )


@pytest.mark.parametrize("marker", ["/Users/", "/home/"])
def test_no_absolute_home_path_is_tracked_in_source_or_config(marker):
    """An absolute home path names a person and their machine. This repo promises neither."""
    out = subprocess.run(
        ["git", "grep", "-l", "-I", "--fixed-strings", marker, "--",
         "sluice", "tests", "*.md", "*.yaml", "*.yml", "*.toml", ".gitignore"],
        cwd=REPO, capture_output=True, text=True, timeout=30)
    hits = [f for f in out.stdout.splitlines()
            # this file necessarily contains the strings it is searching for
            if not f.endswith("test_no_leaked_files.py")]
    assert not hits, f"absolute home path {marker!r} found in tracked files: {hits}"
