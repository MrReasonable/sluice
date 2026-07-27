"""CI's quality bar, pinned against the docs that tell a human to run it.

A lint CI enforces but the docs understate is worse than no change: an agent following
`path-to-green` runs the documented command, passes locally, and lands red. A lint the docs claim
but CI does not enforce is worse still -- the bar reads as covered and isn't.

So this asserts the two halves AGREE. It is deliberately only the cheap half of CI wiring: the
rulesync drift gate's own assertions land with that gate. `scripts/` became a CI lint target ahead
of the gate, so its guard lands ahead of the gate too. `tests/test_hooks_wiring.py` records what
happens otherwise -- "a correct guard that is not wired is inert, and this exact file has already
shipped inert once".

WHY TEXT, NOT A YAML PARSE: pyyaml is a guarded optional import in `sluice/` (CLAUDE.md's
stdlib-only rule), so a test needing it is a test that can skip itself into uselessness on a bare
install. What is being pinned is a command STRING, which text matching pins exactly.

THE THREE CASES, and why prose is not simply skipped. `ruff check` appears in the docs as a
command (`ruff check sluice tests scripts`), and as prose that names no targets at all -- a
graphviz node label reading `ruff check + pytest`. Skipping every no-target match would open the
hole this module exists to close: a command degraded to a bare `ruff check` would be reclassified
as prose and silently pass. So a match with no targets is only tolerated when a CONNECTOR follows
(`+`, `and`), which a shell command cannot start with; anything else with no targets fails.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
RULESYNC = ROOT / ".rulesync"

# The lint targets CI enforces. `scripts/` is here because guard scripts are production code --
# `.rulesync/rules/CLAUDE.md` puts `scripts/` under the mutation-testing bar -- and holding a
# merge gate to a lower standard than the code it guards is the wrong way round.
REQUIRED_TARGETS = ("sluice", "tests", "scripts")

# Stop at anything that ends a shell word list: markdown/code punctuation, or a shell operator.
_RUFF_CHECK = re.compile(r"ruff check(?P<rest>[^\n`|&;\"')]*)")
# Prose connectors a real invocation can never begin with.
_CONNECTORS = ("+", "and", "&")


def _targets(rest: str) -> list[str]:
    """Target paths in a `ruff check` argument string, with flags (`--fix`) dropped."""
    return [tok for tok in rest.split() if not tok.startswith("-")]


def _assert_bar_is_complete(text: str, where: str) -> int:
    """Assert every `ruff check` in `text` names all REQUIRED_TARGETS. Returns the match count."""
    matches = list(_RUFF_CHECK.finditer(text))
    for match in matches:
        rest = match.group("rest").strip()
        # Classify BEFORE counting targets: a connector like `+` is not a `-` flag, so it would
        # otherwise be counted as a lint target and the match misreported as a broken command.
        if rest.startswith(_CONNECTORS):
            continue  # Prose naming the tool ("ruff check + pytest"), not stating the bar.
        targets = _targets(rest)
        if not targets:
            # An inline code span -- `ruff check` wrapped in backticks -- names the tool in a
            # sentence. A command degraded to a bare `ruff check` is NOT wrapped, so the wrapping
            # is what separates prose from the regression this test exists to catch.
            wrapped = text[match.start() - 1 : match.start()] == "`" and (
                text[match.end() : match.end() + 1] == "`"
            )
            assert wrapped, (
                f"{where}: `ruff check` names no lint targets and is not an inline code span "
                f"(rest={rest!r}). A command degraded to a bare `ruff check` lints nothing."
            )
            continue
        for required in REQUIRED_TARGETS:
            assert required in targets, (
                f"{where}: quality bar drops {required!r} (targets={targets}). "
                "An agent following it passes locally and lands red in CI."
            )
    return len(matches)


def test_ci_lints_every_required_target():
    # Non-vacuity: without this, a rename that stops the regex matching passes silently.
    assert _assert_bar_is_complete(CI.read_text(), str(CI)), (
        f"no `ruff check` found in {CI}: this gate would pass without having checked anything"
    )


def test_every_documented_quality_bar_matches_ci():
    """The docs an agent actually follows must name the same targets CI enforces.

    Enumerated from the canonical tree, never hand-listed: a written-out file list goes stale the
    moment a skill is added, and stale-and-green is exactly the failure this module prevents.
    """
    total = 0
    for path in sorted(RULESYNC.rglob("*")):
        if path.is_file():
            total += _assert_bar_is_complete(path.read_text(), str(path.relative_to(ROOT)))
    assert total, (
        f"no `ruff check` found anywhere under {RULESYNC}: either the docs stopped stating the "
        "quality bar, or this sweep stopped finding it -- both leave the bar unguarded"
    )


def _ci_text() -> str:
    return CI.read_text()


def test_ci_success_requires_the_rulesync_job():
    """Membership asserted SEPARATELY from the consistency check below.

    Consistency alone is not enough: deleting the `needs:` entry AND its conjunct together
    leaves both sides agreeing while the gate is unwired.
    """
    text = _ci_text()
    assert "rulesync" in text.split("ci-success:")[1].split("if:")[0], (
        "ci-success does not depend on the rulesync job"
    )
    assert 'needs.rulesync.result }}" = success' in text, (
        "ci-success does not CHECK the rulesync result. `if: always()` means `needs:` only "
        "orders the job -- the && chain is the only thing that can fail ci-success, so a red "
        "gate would yield a green required check."
    )


def test_every_needed_job_is_checked_in_the_success_chain():
    """Both ends enumerated from the file, never hand-listed."""
    block = _ci_text().split("ci-success:")[1]
    needed = re.search(r"needs:\s*\[([^\]]*)\]", block).group(1)
    jobs = [j.strip() for j in needed.split(",") if j.strip()]
    assert jobs, "ci-success declares no needs: this test would pass without checking anything"
    for job in jobs:
        assert f'needs.{job}.result }}}}" = success' in block, (
            f"ci-success needs {job!r} but never checks its result"
        )


def test_the_rulesync_job_sets_pipefail():
    """`bash -e {0}` is the default -- `-e` but NOT pipefail, so `cmd | tee` reports tee's
    status and a rulesync exit 1 would be swallowed."""
    assert "set -euo pipefail" in _ci_text()


def test_the_capture_file_is_outside_the_work_tree():
    """A scratch file beside the checkout is itself untracked and trips the porcelain check."""
    assert '$RUNNER_TEMP/rulesync-output.txt' in _ci_text()


def test_the_guard_runs_before_the_porcelain_check():
    """Index comparison, not substring presence: a substring test passes when EITHER is
    deleted. A fail-open produces a CLEAN tree, so porcelain-first would pass on it."""
    text = _ci_text()
    guard_at = text.index("guard_rulesync_drift.py")
    porcelain_at = text.index("git status --porcelain")
    assert guard_at < porcelain_at, "the completeness guard must run before the tree check"


def test_the_job_uses_the_locked_binary_not_npx():
    """Substituting `npx rulesync@<pinned-version> generate ...` back keeps the counts identical
    and the guard green while silently discarding the locked transitive tree the pin exists for.

    Deliberately no literal version digit here: tests/test_rulesync_version_pin.py enforces that
    package.json is the ONLY tracked file allowed to name the pinned rulesync version, and this
    docstring is not on its allowlist.
    """
    text = _ci_text()
    assert "npm ci --ignore-scripts" in text
    assert "npx" not in text, "the CI job must not invoke npx: it can fetch an unpinned rulesync"
    assert text.index("npm ci --ignore-scripts") < text.index("guard_rulesync_drift.py")


def test_package_json_runs_the_locked_binary_by_path():
    """`npm run` PREPENDS node_modules/.bin to PATH, it does not restrict PATH. Measured: with
    no node_modules, a bare `rulesync` silently ran a global 9.2.0 and exited 0."""
    manifest = json.loads((ROOT / "package.json").read_text())
    assert manifest["scripts"]["rulesync"] == "node_modules/.bin/rulesync generate -t '*' -f '*'"
