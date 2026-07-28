#!/usr/bin/env python3
"""Assert `rulesync generate` produced every output it was supposed to.

WHY COUNTS AND NOT JUST A CLEAN TREE. A malformed `.rulesync/hooks.json` makes rulesync drop 17
files, exit **0**, and leave `git status --porcelain` **empty**. Exit code and porcelain both
pass green on the one failure this gate exists to catch. Reproduced again on a real version
delta: an older rulesync against a .gitignore audited for the pinned version emitted 238 files
with a clean tree.

WHY EXACT COUNTS AND NOT "NON-ZERO". A partial drop -- 16 of 17 hooks -- passes a non-zero
check. The output set is fully determined by tracked inputs (`.rulesync/` AND
`package-lock.json`, which is why the lockfile exists), so an exact pin is legitimate.

WHEN EXPECTED CHANGES. Only when `.rulesync/` or the lockfile changes. Updating it is the moment
to re-audit `.gitignore`'s generated-output list, because a version bump is exactly when a new
target or feature starts emitting into a path nothing ignores. A Dependabot rulesync PR is
therefore EXPECTED to arrive red: that is the gate working, not a defect to debug away.

THE COUNTS ARE ONLY VALID ON A FRESH TREE. rulesync skips writing a file whose content already
matches, so re-running in a working copy reports a handful of files rather than the full set.
"""

from __future__ import annotations

import re
import sys

EXPECTED = {"rules": 2, "subagents": 5, "skills": 4, "hooks": 1}

_SUMMARY = re.compile(r"All done! Written \d+ file\(s\) total \(([^)]*)\)")
_TERM = re.compile(r"(\d+)\s+([a-z]+)")
# What may legitimately sit between two terms. rulesync joins with ` + `; whitespace alone is
# admitted because a bare join is still unambiguous, and pinning the exact separator would turn
# a cosmetic reformat into a parse error with no drift behind it.
#
# TWO patterns, not one, and the difference is load-bearing. BETWEEN two terms a separator is
# REQUIRED: with `*` the gap may be empty, so `20 rules114 subagents92 skills17 hooks` parsed as
# four clean terms and this guard reported a match on output it had not understood. But an empty
# gap is CORRECT in the two places nothing needs separating -- before the first term, and after
# the last -- so `+` cannot simply replace `*` at every site: that would reject every real
# summary, whose trailing remainder is always empty.
_SEPARATOR_BETWEEN = re.compile(r"[\s+]+")
_SEPARATOR_EDGE = re.compile(r"[\s+]*")


def parse_summary(text: str) -> dict[str, int]:
    """Feature -> count, parsed from rulesync's `All done!` line. Pure.

    A feature that produced nothing is OMITTED from the parenthetical rather than printed as
    `0 hooks`. So the returned map simply lacks that key; `main` is what turns absence into a
    failure. A caller that regexes `(\\d+) hooks`, finds no match, and moves on would report
    success on exactly the input this guard exists to reject.

    The parenthetical must be FULLY CONSUMED, and no feature may appear twice. A find-all over
    `(\\d+) ([a-z]+)` is happy to skip whatever it does not understand, which made this guard
    quietly more permissive than every claim it makes about itself: `(... + 17 hooks +
    NONSENSE)` parsed clean with NONSENSE discarded, and `(... + 17 hooks + 99 hooks)` yielded
    `hooks: 99` on last-wins. Both are exactly the "the output format changed on a version
    bump" case the error below promises to fail loudly on.
    """
    found = _SUMMARY.findall(text)
    if not found:
        raise ValueError(
            "no `All done!` summary line in the captured output. Causes, commonest first: the "
            "tree was ALREADY generated, so rulesync wrote nothing and printed its other summary "
            "form, `All files are up to date (...)` -- these counts are only valid on a FRESH "
            "checkout; rulesync did not finish; the capture is empty; or the output format "
            "changed on a version bump. Any of those must fail loudly rather than pass."
        )
    if len(found) > 1:
        raise ValueError(f"found {len(found)} summary lines, expected exactly 1")

    inner = found[0]
    counts: dict[str, int] = {}
    cursor = 0
    for index, term in enumerate(_TERM.finditer(inner)):
        gap = inner[cursor : term.start()]
        # Nothing precedes the first term, so only later gaps must actually separate something.
        if not (_SEPARATOR_EDGE if index == 0 else _SEPARATOR_BETWEEN).fullmatch(gap):
            # Distinguish the two shapes: an EMPTY gap means two terms ran together, which reads
            # nothing like "unparsed text ''" to whoever hits it at 2am.
            detail = (
                "two terms run together with no separator between them"
                if gap == ""
                else f"unparsed text {gap!r}"
            )
            raise ValueError(
                f"{detail} in the summary parenthetical {inner!r}. Accepting it would report a "
                "clean parse of output this guard does not understand."
            )
        name, count = term.group(2), int(term.group(1))
        if name in counts:
            raise ValueError(
                f"feature {name!r} appears twice in the summary parenthetical {inner!r}. "
                "Last-wins would silently pick one count and call the other a match."
            )
        counts[name] = count
        cursor = term.end()

    trailing = inner[cursor:]
    if not _SEPARATOR_EDGE.fullmatch(trailing):
        raise ValueError(
            f"unparsed trailing text {trailing!r} in the summary parenthetical {inner!r}. "
            "Discarding it would report a clean parse of output this guard does not understand."
        )
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: guard_rulesync_drift.py <captured-rulesync-output>", file=sys.stderr)
        return 2
    try:
        text = open(argv[0], encoding="utf-8").read()
    except OSError as exc:
        print(f"cannot read the captured output: {exc}", file=sys.stderr)
        return 2
    try:
        actual = parse_summary(text)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    # Absent means zero, so a silently-dropped feature MISMATCHES rather than being skipped.
    normalised = {feature: actual.get(feature, 0) for feature in EXPECTED}
    unexpected = sorted(set(actual) - set(EXPECTED))
    if normalised == EXPECTED and not unexpected:
        return 0

    print("rulesync output does not match the pinned expectation.", file=sys.stderr)
    for feature in sorted(EXPECTED):
        print(f"  {feature:<10} expected {EXPECTED[feature]:>4}  actual {normalised[feature]:>4}",
              file=sys.stderr)
    for feature in unexpected:
        print(f"  {feature:<10} UNEXPECTED feature, emitting {actual[feature]} file(s) -- it "
              f"writes to paths .gitignore may not cover", file=sys.stderr)
    print(
        "\nIf .rulesync/ or package-lock.json changed, update EXPECTED -- and re-audit "
        "the generated-output list in .gitignore while you are there.\n"
        "If neither changed, generation is broken.\n"
        "NOTE: these counts are only valid on a FRESH checkout; rulesync skips files whose "
        "content already matches, so a re-run in a working copy reports far fewer.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
