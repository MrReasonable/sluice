"""One place chooses the rulesync version; everywhere else agrees or says nothing.

Before this, a hardcoded version string sat in several live places and a bump meant several
edits kept in step by nothing -- including, self-referentially, this file's own docstring: the
sweep below treats this module as a tracked file like any other, so naming the literal here
would fail on itself the moment it was committed. package.json is now the only place that
CHOOSES. One exception is deliberate:
.rulesync/hooks.json's `_comment` records which version's schema that file was written
against, and that comment calls itself the only defence against a version bump silently
dropping the hook command. Erasing the literal would lose the record; excluding the file
would make this sweep vacuous exactly where it matters. So it is ASSERTED EQUAL instead --
which means a bump turns this test red until a human re-verifies the emitted settings.json,
precisely what that comment asks for and today cannot enforce.

docs/superpowers/ is excluded WHOLESALE, by directory -- `_tracked_files()` drops the entire
prefix unconditionally, not on any per-file date. That is broader than "a dated record" sounds:
this branch's own plan and spec live under it and name the version repeatedly while the work is
still in flight, well before either could be called dated. The exclusion holds anyway because
nothing under docs/superpowers/ is canonical guidance a maintainer or agent follows day to day --
it is process documentation (plans, specs, design records), which this repo's own convention
already treats as point-in-time: superseded with a dated note rather than rewritten. A version
literal sitting there is not read as current instruction the way a hit in .rulesync/ or a skill
would be, so this sweep does not need to see it.
"""

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+\b")
ALLOWED = {"package.json", "package-lock.json", ".rulesync/hooks.json"}

# Any rulesync-associated semver -- e.g. `rulesync@X.Y.Z` or `rulesync X.Y.Z` -- not just the
# CURRENTLY pinned value. A value match only catches a doc that repeats today's version; a doc
# that still names the OLD one after a bump contains a string the value match would never look
# for. Anchored on the literal word "rulesync" so this does not fire on an unrelated pin like
# `ruff==0.15.21`, which carries a semver but never the word "rulesync" near it.
RULESYNC_VERSION_RE = re.compile(r"rulesync[@ -]v?\d+\.\d+\.\d+\b", re.IGNORECASE)


def _pinned_version() -> str:
    manifest = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    return manifest["devDependencies"]["rulesync"]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=30, check=True
    ).stdout
    return [p for p in out.splitlines() if not p.startswith("docs/superpowers/")]


def test_the_pinned_version_is_readable_and_specific():
    """Non-vacuity: every assertion below compares against this string."""
    version = _pinned_version()
    assert VERSION_RE.fullmatch(version), f"package.json pins a non-specific version: {version!r}"


def test_hooks_json_records_the_version_it_was_verified_against():
    comment = json.loads((REPO / ".rulesync" / "hooks.json").read_text(encoding="utf-8"))["_comment"]
    found = VERSION_RE.findall(comment)
    assert found, (
        ".rulesync/hooks.json's _comment no longer records which rulesync version its schema was "
        "verified against. That record is the only defence against a bump silently dropping the "
        "hook command -- restore it rather than deleting it."
    )
    for version in found:
        assert version == _pinned_version(), (
            f".rulesync/hooks.json says {version}, package.json pins {_pinned_version()}. "
            "Re-verify the emitted .claude/settings.json by hand, then update the comment."
        )


def test_no_other_tracked_file_names_a_rulesync_version():
    """Catches ANY rulesync-associated version, not only the one currently pinned.

    A sweep keyed on `_pinned_version()`'s exact value goes blind the moment package.json is
    bumped: a doc that still names the OLD version becomes invisible to a check that only ever
    looks for the new one. `RULESYNC_VERSION_RE` instead matches the SHAPE (the word "rulesync"
    immediately adjacent to a semver), so a stale reference is caught whether it agrees with
    today's pin, yesterday's, or neither.
    """
    files = _tracked_files()
    assert files, "git ls-files returned nothing: this sweep would pass without checking"
    offenders = [
        path
        for path in files
        if path not in ALLOWED
        and RULESYNC_VERSION_RE.search((REPO / path).read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, (
        f"these tracked files name a rulesync version: {offenders}. "
        "package.json is the only place that chooses it; prose should name no version at all."
    )
