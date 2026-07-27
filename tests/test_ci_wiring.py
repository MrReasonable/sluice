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
