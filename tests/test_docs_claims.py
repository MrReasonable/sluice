"""Every documented `job-sluice` invocation must be a real command, and every real command must
be documented -- enumerated from the actual argparse tree (`sluice.cli._build_parser()`), never
hand-listed. A hand-listed enumeration is exactly the shape that has gone stale silently
elsewhere in this project: it agrees with the code on the day it's written and says nothing the
day a command is renamed, added, or removed.

This is the guard that would have caught two real defects found while writing the docs this file
now watches: `CHANGELOG.md` naming the wrong default renderer and instructing a retired
`cv.renderer: weasyprint` (a name that raises), and several `pip install 'sluice[extra]'`
instructions that -- after the PyPI distribution was renamed to `job-sluice` -- would have quietly
targeted an unrelated, real PyPI package instead of failing loudly. Both classes get a permanent
sweep here rather than a one-time fix.
"""
import argparse
import glob
import inspect
import pathlib
import re

import pytest

from sluice import cli
from sluice.cli import _build_parser
from sluice.core.protocols import EVIDENCE_KINDS
from tests.markdown_fences import strip_fenced_blocks, unclosed_fence

# What a user reads. Same shape as tests/test_no_copy_instruction.py's `_SHIPPED_PROSE`: docs
# under superpowers/ are deliberately excluded there and here, for the same reason -- they are
# historical design records that necessarily quote retired names and old invocations as part of
# documenting how a decision was reached, and sweeping them would force a design doc to lie about
# its own history.
#
# CHANGELOG.md, sluice.yaml.example and pyproject.toml were MISSING from this list for exactly
# as long as this module's own docstring above claimed they were covered: a `pip install
# 'sluice[render]'` regression shipped in CHANGELOG.md in the very same commit that fixed the
# same bug everywhere else, and this sweep -- the guard written specifically to catch that class
# -- said nothing, because none of the three lived under README.md/CONTRIBUTING.md/SECURITY.md/
# docs/*.md/.rulesync/**/*.md. Found in review, not by this test, which is the failure mode a
# guard's own scope gap always produces: silently correct on every file it happens to read.
_DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "sluice.yaml.example",
         "pyproject.toml"]
_DOCS += glob.glob("docs/*.md")
_DOCS += glob.glob(".rulesync/**/*.md", recursive=True)


def _read_all():
    """(path, text) for every doc that exists, plus a count of how many were readable.

    Returned together rather than as two separate walks, so a test can assert on the SCOPE
    (the count) without re-reading the filesystem -- a sweep that silently reads zero files
    passes every assertion made over an empty set (`all([])` is `True`), which is exactly the
    failure shape this project has been bitten by before.
    """
    out = []
    for path in sorted(set(_DOCS)):
        try:
            out.append((path, open(path, encoding="utf-8").read()))
        except OSError:
            continue
    return out


def _command_tree():
    """{group: None} for a leaf group (health/init/doctor -- no subcommands of their own),
    {group: [sub, ...]} otherwise. Walked from the real parser via argparse's own
    `_SubParsersAction`/`.choices`, the same private-API shape `argcomplete` itself relies on to
    introspect an arbitrary argparse tree -- so this cannot drift from what `--help` shows.
    """
    parser = _build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    tree = {}
    for name, sub in top.choices.items():
        sub_sp = next(
            (a for a in sub._actions if isinstance(a, argparse._SubParsersAction)), None)
        tree[name] = sorted(sub_sp.choices) if sub_sp is not None else None
    return tree


# Captures 1-2 bare lowercase-hyphen tokens after `job-sluice` -- exactly the shape every real
# group and subcommand name takes (`list-sources`, `test-source`, `normalize-status`, ...).
# A flag (`--offline`), an env var (`$VAULT_DIR`), a placeholder (`<group>`), or a path
# (`./vault`) cannot match the character class, so ordinary prose ("job-sluice reads
# $XDG_CONFIG_HOME...") never gets treated as a command claim -- verified empirically against
# every doc in this repo before this test was written (see the PR that added it).
_INVOCATION = re.compile(r"\bjob-sluice\b((?:\s+[a-z][a-z-]*){1,2})")


def _claimed_pairs(text: str) -> set:
    """Every (group, subcommand-or-None) pair `text` appears to invoke."""
    pairs = set()
    for m in _INVOCATION.finditer(text):
        tokens = m.group(1).split()
        pairs.add((tokens[0], tokens[1] if len(tokens) > 1 else None))
    return pairs


def test_the_command_tree_walk_is_not_vacuous():
    """SCOPE guard, ahead of everything below: a broken walk (e.g. `_build_parser` changing
    shape so `_SubParsersAction` is never found) would make every assertion below pass over an
    empty tree. Pin real structure, not just non-emptiness -- 13 known top-level groups (#164
    added `experience`/`skills`/`stories`, one loop over EVIDENCE_KINDS, to the prior 10) and an
    EXACT total subcommand count, not merely a floor.

    The prior `>= 15` floor was itself the bug this file exists to catch: the real count moved
    from 20 to 29 across #164 and the floor caught none of it (Task 7 review, MINOR 4) -- a
    floor that trails reality by 14 asserts nothing. The literal (`21`) is the NON-EVIDENCE
    groups' own subcommand total; the evidence contribution is DERIVED from EVIDENCE_KINDS (3
    subcommands -- add/list/verify -- per kind) so a future fourth kind needs no edit here.

    The literal is edited by hand ON PURPOSE, and #241 is the worked example: it was `20` until
    `leads add` made it 21, and this assertion is what said so. An earlier version of this note
    justified the literal by claiming the count "does NOT grow on its own" -- which read as a
    property of the tree when it is only a property of the LITERAL, and would have invited
    deriving it from the walk the next time it moved. A derived expected value compares the walk
    against itself and can never fail. The edit IS the review step: a subcommand is a public
    interface, and being made to touch this line is how a new one is noticed here, in
    docs/USAGE.md, and in README's Commands table together.
    """
    tree = _command_tree()
    assert set(tree) == {
        "ingest", "triage", "cv", "apply", "track", "leads", "health", "mcp", "init", "doctor",
        "experience", "skills", "stories"}, (
        f"the walk found {sorted(tree)} -- a group was added, renamed, or removed; if that is "
        f"intentional, docs/USAGE.md and this set both need updating")
    total_subs = sum(len(v) for v in tree.values() if v is not None)
    expected = 21 + 3 * len(EVIDENCE_KINDS)
    assert total_subs == expected, (
        f"expected {expected} subcommands (21 non-evidence + 3 per evidence kind), found "
        f"{total_subs} -- the walk is broken, or a group's own subcommand count changed and "
        f"this needs updating, along with docs/USAGE.md and README's Commands table")


# English number words for the prose sweep below. This is a fixed VOCABULARY (spellings of
# small integers never drift the way a business-domain hand-list does), not a registry that
# needs to grow in lockstep with the product -- it only needs headroom past however many
# top-level groups this project plausibly reaches, so twenty is ample rather than exact.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def test_the_top_level_group_count_prose_matches_the_real_tree():
    """docs/USAGE.md opens with a spelled-out count ("Thirteen top-level command groups") that
    #164's review flagged as an unguarded claim of exactly the kind this whole file exists to
    catch (MINOR 5): nothing checked it against the code, so it would go stale silently the
    next time a group is added or removed -- precisely the CHANGELOG-renderer-default shape
    this module's own docstring names as its reason for existing.
    """
    tree = _command_tree()
    text = open("docs/USAGE.md", encoding="utf-8").read()
    m = re.search(r"\b([A-Za-z]+) top-level command groups\b", text)
    assert m is not None, (
        "docs/USAGE.md's top-level-group-count sentence is missing or reworded -- update this "
        "sweep's pattern, or restore a spelled-out count sentence")
    word = m.group(1).lower()
    assert word in _NUMBER_WORDS, (
        f"docs/USAGE.md's group count is spelled {word!r}, which this sweep does not "
        f"recognize as a number word -- widen _NUMBER_WORDS")
    assert _NUMBER_WORDS[word] == len(tree), (
        f"docs/USAGE.md says {word!r} top-level command groups, but the real parser tree has "
        f"{len(tree)} -- update the prose")


def test_every_documented_command_claim_is_real():
    """A doc claiming `job-sluice <group> <subcommand>` where that pair does not actually exist
    is the exact CHANGELOG-renderer-default shape of bug this file exists to catch -- a claim
    that was true once (or never was) and nothing since has checked it against the code.

    A claimed GROUP that is not a real group name is silently skipped rather than failed: that
    is ordinary prose ("...gives you on $PATH, so nothing here reasons about..."), not a claim
    about a command. Only once the group itself is real does a second word get checked, and even
    then only for a group that HAS subcommands -- a leaf group (health/init/doctor) followed by
    an ordinary English word ("job-sluice init resolves...") is not a subcommand claim.
    """
    tree = _command_tree()
    checked = 0
    for path, text in _read_all():
        checked += 1
        for group, sub in _claimed_pairs(text):
            if group not in tree or sub is None:
                continue
            subs = tree[group]
            if subs is None:
                continue  # leaf group: the second word is incidental prose, not a claim
            assert sub in subs, (
                f"{path} claims `job-sluice {group} {sub}`, which is not a real subcommand of "
                f"{group} (real ones: {', '.join(subs)}). Either the doc is stale or the CLI "
                f"changed and docs/USAGE.md needs updating.")
    assert checked >= 5, f"the sweep read only {checked} files"  # SCOPE, not just non-empty


def test_every_real_command_is_documented_in_usage_md():
    """The converse of the test above: a command that exists but appears nowhere in
    docs/USAGE.md is the silent-omission direction of the same drift -- a new subcommand ships
    and the CLI reference simply never mentions it.
    """
    tree = _command_tree()
    text = open("docs/USAGE.md", encoding="utf-8").read()
    claimed = _claimed_pairs(text)
    missing = []
    for group, subs in tree.items():
        if subs is None:
            if not re.search(rf"\bjob-sluice\b\s+{re.escape(group)}\b", text):
                missing.append(group)
            continue
        for sub in subs:
            if (group, sub) not in claimed:
                missing.append(f"{group} {sub}")
    assert not missing, f"docs/USAGE.md never mentions: {missing}"


# README's Commands table, as {group: {subcommand, ...}}. A dedicated parse rather than
# `_claimed_pairs`, which cannot see this shape: the table names a group once as
# `job-sluice ingest` and then lists its subcommands as bare backticked tokens in the NEXT
# cell (`list-sources`, `run`, ...), never re-prefixed with `job-sluice`. Sweeping the row's
# own cells is what makes the subcommand half of this checkable at all.
#
# SCOPED to the `## Commands` section, and fences are stripped from the WHOLE FILE BEFORE that
# section is located -- not from the section afterwards. The row pattern matches any markdown
# row shaped like one, so a table ANYWHERE else in README would be parsed as though it were the
# real thing. Two measured masking defects, one per cut:
#   1. Parsing the whole file into a last-wins dict let a later fenced row MASK a stale real
#      row, and the guard stayed green on a stale README.
#   2. Stripping fences only from the section body still searched RAW text for the heading, so
#      a fenced `## Commands` plus a correct table EARLIER in the file won the section search
#      and the real, stale table below was never read. Same defect one level up.
# A duplicate row raises rather than quietly winning, for the same reason.
#
# Fence handling lives in `tests/markdown_fences.py`, implemented line by line against
# CommonMark 4.5 and shared with tests/test_fixture_name_neutrality.py. It was a regex here
# until PR #222 round 6, and CodeRabbit corrected that regex on three consecutive rounds --
# one spec clause each time (same-delimiter closer and run length, then a 0-3 space indent,
# then a backtick fence's info string). Round 4 tried to stop the drift by pinning this
# pattern to its sibling; round 5 walked past that, because both were wrong identically.
# One implementation, each rule a named branch, is the fix that generalises.
_README_COMMANDS_SECTION = re.compile(r"^## Commands\s*$(?P<body>.*?)(?=^## |\Z)", re.M | re.S)
_README_COMMAND_ROW = re.compile(r"^\|\s*`job-sluice\s+([a-z][a-z-]*)`\s*\|(.*)\|\s*$", re.M)
_BACKTICKED = re.compile(r"`([a-z][a-z-]*)`")


def _readme_command_table() -> dict:
    with open("README.md", encoding="utf-8") as f:
        text = f.read()
    # Reported as a fact by the scanner, not inferred from leftover delimiter markers. The
    # marker heuristic this replaces only worked while a malformed opener still LOOKED like
    # one: a mixed-delimiter closer and a backtick-in-info-string opener each left ZERO
    # residual markers while swallowing the real Commands section whole.
    assert not unclosed_fence(text), (
        "README has an unclosed code fence. Refusing to parse: an unclosed fence runs to the "
        "end of the document, so everything after it -- the real Commands table included -- is "
        "inside it, and a `## Commands` heading in an earlier illustration would win the "
        "section search unopposed.")
    defenced = strip_fenced_blocks(text)
    # Exactly ONE `## Commands` heading. The section search takes the first match, so a second
    # top-level Commands section carrying a correct table MASKS a stale real one below it --
    # measured green before this line, and found while witnessing the fence fixes rather than
    # reported by any review round. It is the same masking class the fence work closed, minus
    # the fence: duplicate rows already raise rather than last-wins, and a duplicate SECTION
    # has to for the same reason.
    headings = re.findall(r"^## Commands\s*$", defenced, re.M)
    assert len(headings) == 1, (
        f"README has {len(headings)} `## Commands` headings outside code fences. The parse "
        f"takes the first, so a second one would silently decide what this guard checks.")
    section = _README_COMMANDS_SECTION.search(defenced)
    assert section, (
        "README has no `## Commands` heading -- without it this parse would fall back to the "
        "whole file and pick up any table-shaped row anywhere in it")
    table = {}
    for row in _README_COMMAND_ROW.finditer(section.group("body")):
        group, cells = row.group(1), row.group(2)
        assert group not in table, (
            f"README's command table has two rows for `{group}` -- one would mask the other, "
            f"so this is an error rather than last-wins")
        table[group] = set(_BACKTICKED.findall(cells))
    return table


def test_readmes_command_table_covers_every_real_group_and_subcommand():
    """README's command TABLE drifts on its own, and nothing checked the table as a table.

    Precisely, because the loose version of this sentence was itself wrong: the parser WAS
    already walked against README by `test_every_documented_command_claim_is_real` above,
    which sweeps every file in `_DOCS` for `job-sluice <group> <sub>` invocations. What that
    cannot see is the table's own shape -- a row names its group once and lists its
    subcommands as bare backticked tokens in the next cell, an adjacency that sweep never
    matches -- nor the real->claimed direction, which is what a completeness index needs.

    Measured on the pre-#221 file: the table listed TEN groups and the prose said "Ten
    top-level command groups", while the real tree had THIRTEEN -- `experience`, `skills` and
    `stories` shipped with #164 and README was never updated. The `leads` row was stale in the
    subcommand direction too, naming four of its five (`rename` was absent). Both went
    unnoticed because `test_every_real_command_is_documented_in_usage_md` above reads
    docs/USAGE.md ONLY, which did carry all of them -- so the CLI reference was right and the
    front page, which is also the PyPI description, was wrong.

    Completeness in BOTH directions is the point: the table is an index of the command
    surface, so a group or subcommand that exists and is not in it is a silent omission, and
    one that is in it and does not exist is a stale claim. Deliberately no COUNT is asserted
    and README no longer states one -- a spelled-out number in prose is the drift surface this
    repo keeps rediscovering, and for a number that earns a reader nothing, removing it beats
    guarding it.
    """
    tree = _command_tree()
    table = _readme_command_table()
    # SCOPE. NOT what stops this going vacuous: the group set-equality below already fails on
    # an empty table -- measured, by deleting this line and breaking the table, which died
    # there rather than here. It is kept for the DIAGNOSIS, which names the parse instead of
    # reporting thirteen simultaneously-absent groups.
    assert table, "README's command table did not parse"

    assert set(table) == set(tree), (
        f"README's command table lists {sorted(table)} but the real parser tree has "
        f"{sorted(tree)}. Missing from README: {sorted(set(tree) - set(table))}; "
        f"claimed by README but not real: {sorted(set(table) - set(tree))}")

    for group, subs in tree.items():
        if subs is None:
            # Leaf group (health/init/doctor): nothing to enumerate, so the row must name
            # nothing. `continue` alone was the SAME hole as the intersection below, one
            # branch over -- measured, a fabricated `` `frobnicate` `` in the `init` row
            # passed the whole suite while the identical token in a non-leaf row failed.
            # "Both directions" has to hold for every row, not for the rows that happen to
            # reach the comparison.
            assert not table[group], (
                f"README's `{group}` row backticks {sorted(table[group])}, but `{group}` has "
                f"no subcommands at all -- a leaf row may name none")
            continue
        # EQUALITY, never `table[group] & set(subs)`. An intersection discards a README token
        # naming no real subcommand BEFORE comparing, so the check collapses to a subset test
        # and a fabricated `leads frobnicate` passed the whole suite -- measured, by three
        # reviewers independently, on this guard's first cut. The cost of equality is that a
        # row's cells may backtick ONLY real subcommand names; a flag cannot collide, since
        # `_BACKTICKED` requires a leading letter and so never matches `--write`.
        assert table[group] == set(subs), (
            f"README's `{group}` row names {sorted(table[group])} but the real subcommands "
            f"are {sorted(subs)} -- missing: {sorted(set(subs) - table[group])}; "
            f"claimed but not real: {sorted(table[group] - set(subs))}")


# Config keys that RAISE if set (retired), keyed to the exact shape a doc would write them in.
# Each tuple is (compiled pattern, what's wrong, a POSITIVE sample the pattern must match --
# see test_every_retired_config_pattern_matches_its_own_sample below). `cv.renderer: weasyprint`
# is the literal CHANGELOG defect this file was written after finding; the others are the same
# class of key CLAUDE.md documents as retired-and-raising elsewhere, swept here so a future doc
# cannot reintroduce any of them by the same silent-drift path.
#
# The sample lives IN the tuple, not in a parallel index-aligned list: a second list that must
# stay the same length and order as this one is exactly the hand-maintained-copy shape this
# codebase avoids elsewhere (dataclasses.fields() over a hand list, DERIVED _WARNED_KEYS, ...) --
# reordering one entry here would silently pair a sample with the wrong pattern instead of
# failing to parse.
_RETIRED_CONFIG = [
    (re.compile(r"cv\.renderer:\s*weasyprint\b"),
     "cv.renderer: weasyprint -- retired, raises naming `template` as the replacement",
     "Install the bundled renderer and set cv.renderer: weasyprint in your config."),
    (re.compile(r"\bcv\.baseline_rel\b"),
     "cv.baseline_rel -- retired, baseline_rel is a ROOT config key now",
     "Set cv.baseline_rel: ./baseline.md if migrating from an old config."),
    (re.compile(r"\btriage\.dossier_dir\b"),
     "triage.dossier_dir -- retired, use the root dossier_dir",
     "The old triage.dossier_dir key has moved to the root dossier_dir."),
    (re.compile(r"\bcv\.dossier_dir\b"),
     "cv.dossier_dir -- retired, use the root dossier_dir",
     "Set cv.dossier_dir: ./dossiers to override the shared cache location."),
    # cv.name/cv.contact (#133/#107) are the newest members of this class -- identity moved
    # to the vault's Candidate Profile note, and a config still setting either now raises.
    # Unlike the three entries above, these are deliberately INSTRUCTION-shaped (a colon
    # followed by a value, mirroring cv.renderer's own value-bearing pattern above) rather
    # than a bare `\bcv\.name\b`: docs/TROUBLESHOOTING.md's migration section legitimately
    # NAMES both retired keys in prose (to tell a reader what to remove), and a bare pattern
    # would flag that correct, necessary mention as if it were an instruction to set them.
    # A colon-plus-value is the shape an actual instruction to CONFIGURE the key would take;
    # prose that merely names the key for removal never writes that shape -- pinned by
    # test_the_migration_removal_sentence_does_not_trip_the_retired_config_sweep below, not
    # merely asserted in this comment.
    #
    # Narrowing this way leaves these two patterns blind to the indented-YAML shape
    # `cv:\n  name: "Ada Example"` -- the shape an actual config example takes, and the one
    # `sluice.yaml.example` writes commented (`# cv:` / `#   name: ...`), where
    # tests/test_config_example.py's unknown-key sweep also `continue`s past it. That gap is
    # now CLOSED, but not here: widening a regex to see block structure is what would
    # false-positive on unrelated indented content elsewhere on the page, so it is closed by
    # the section-aware line scanner further down this file (`_nested_cv_keys`) instead. Keep
    # these two patterns dotted-only; the nested shape has its own sweep and its own controls.
    # Still uncovered by either, and small enough to leave: a spaced-out `cv.name : "Ada"`.
    (re.compile(r"\bcv\.name:\s*\S"),
     "cv.name: <value> -- retired, identity now lives in the vault's Candidate Profile note",
     'Set cv.name: "Ada Example" in your config.'),
    (re.compile(r"\bcv\.contact:\s*\S"),
     "cv.contact: <value> -- retired, identity now lives in the vault's Candidate Profile note",
     'Set cv.contact: "+44 20 7946 0000" in your config.'),
    # A bare root `locations:` key, NOT `target_locations:`/`reject_locations:`/anything
    # ending in "_locations" -- the lookbehind excludes a preceding word character or dot so
    # those two live, legitimate keys can be documented on the same page without tripping this.
    (re.compile(r"(?<![\w.])locations:"),
     "a root `locations:` key -- retired, use triage.target_locations",
     "locations: [Alfa, Bravo]"),
]


# A `BREAKING CHANGES` block in CHANGELOG.md is the ONE place a shipped doc must be able to
# NAME a retired key, because recording the retirement is the block's entire job: a migration
# note forbidden from spelling the key it retires cannot tell a reader what to delete. The
# `cv.name`/`cv.contact` removal's entry says a `cv.name: ""` left by a half-finished migration
# still raises -- accurate, useful, and matched by `_RETIRED_CONFIG`'s dotted pattern. Left
# unnarrowed, the guard fired on the release PR and blocked it for two days (#170), and would
# fire again on every future release whose changelog names a retired key.
#
# release-please DRAFTS that block from commit footers, but the wording is NOT fixed by the
# generator: CHANGELOG.md's own header, and CLAUDE.md's Conventions, both say the entry is
# edited by hand in the release PR before it is merged, and that editing step is exactly where
# a migration note gets written. So the exemption covers hand-written prose under the heading
# as well as the generated bullets. That is a deliberate part of the trade-off rather than an
# accident of the mechanism -- and it is why the three scoping rules below are what keep the
# exemption affordable, since "the generator controls it" would not.
#
# Scoped as tightly as the failure demands, and no tighter:
#   * CHANGELOG.md STAYS in `_DOCS`. Removing it would reopen the scope gap this file's own
#     header comment records -- a `'sluice[render]'` regression shipped there and this sweep
#     said nothing.
#   * Only the `_RETIRED_CONFIG` sweep skips the block. Every other check in this file still
#     reads the whole changelog, breaking block included.
#   * Only CHANGELOG.md gets the treatment. A BREAKING heading in any other doc is still swept
#     -- pinned by `test_the_narrowing_reaches_changelog_md_and_no_other_doc`, which drives the
#     real sweep body over the SAME text under both filenames.
#
# Parsed explicitly rather than matched by one regex. Four review rounds narrowed a single
# pattern and each spelling was still wider than the failure it was written for:
# `.*BREAKING CHANGES.*` admitted `NOT BREAKING CHANGES`; `\s` admitted a marker spanning
# NEWLINES (a bare `###` above a `BREAKING CHANGES` paragraph is two blocks, not a heading);
# and `#*` admitted `BREAKING CHANGES###`, which is literal text, since a closed-ATX suffix
# needs a space before it. Each fix was the narrowest thing that closed the case in hand, which
# is why a fifth patch is not the answer. Stating the rules separately makes each one checkable
# on its own, and `test_only_an_exact_breaking_heading_is_stripped` carries every near-miss
# found so far as a row.
#
# What each part is for:
#   * `_ATX` -- two to six hashes, then at least one space or tab, all on ONE physical line.
#     The `[ \t]` class rather than `\s` is what refuses a newline-spanning marker; the hash
#     FLOOR of two is a stated decision, not an oversight (see the H1 rows in
#     `test_only_an_exact_breaking_heading_is_stripped`).
#   * `_CLOSING_HASHES` -- closed-ATX (`## BREAKING CHANGES ##`). It REQUIRES the preceding
#     space, which is what keeps `BREAKING CHANGES###` literal text.
#   * `_LEADING_DECORATION` -- release-please emits `### \u26a0 BREAKING CHANGES` (U+26A0), so a
#     run of leading non-word, non-space characters must be tolerated. A WORD before the phrase
#     must not be, which is why the class excludes `\w`.
#   * `_ANY_HEADING` -- what ENDS a block. Any level 1 to 6, including a BARE `#{1,6}` with no
#     text after it: that is a valid empty ATX heading, and one that failed to terminate would
#     silently extend the exemption to the next non-empty heading instead.
#   * `_FENCE` -- see `_without_breaking_blocks`; fenced content is code, not structure.
_ATX = re.compile(r"^(#{2,6})[ \t]+(.*)$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")
_LEADING_DECORATION = re.compile(r"^(?:[^\w\s][ \t]*)+")
_ANY_HEADING = re.compile(r"^#{1,6}(?:[ \t]|$)")
# CommonMark: a fence closes only on the SAME delimiter CHARACTER, at least as long as the
# opener, followed by nothing but whitespace. Toggling on any ``` or ~~~ made a `~~~` line
# INSIDE a ``` block close it, after which a `### BREAKING CHANGES` line still inside that
# code block started a real exemption and could hide a retired-key instruction
# (measured; the sixth spelling of this hole).
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*(.*)$")


def _is_breaking_heading(line):
    """True only for a heading whose text is exactly `BREAKING CHANGES`.

    Tolerates what release-please actually emits (`### \u26a0 BREAKING CHANGES`), closed-ATX
    (`## BREAKING CHANGES ##`), and the trailing whitespace a hand-edit leaves behind (the
    final `.strip()`) -- and nothing else: no word before the phrase, nothing after it, at
    least one space or tab after the hashes, and the whole marker on one physical line.
    """
    m = _ATX.match(line)
    if not m:
        return False
    text = _CLOSING_HASHES.sub("", m.group(2))
    return _LEADING_DECORATION.sub("", text).strip() == "BREAKING CHANGES"


def _without_breaking_blocks(text):
    """`text` with every `BREAKING CHANGES` section removed, bounded by the next heading.

    Returns `text` UNCHANGED when there is no such heading, which is the case on `main` today --
    the generated block only exists on a release branch. That is why the non-vacuity of this
    function is pinned by `test_breaking_block_stripping_is_bounded` against SYNTHETIC input
    rather than against the live file: an assertion that "stripping removed something" would be
    vacuous on main and would start failing for the wrong reason the moment a release lands.

    FENCED content is code, not Markdown structure, so no line inside a ``` or ~~~ fence opens
    or closes a block. Without that, a `### BREAKING CHANGES` line QUOTED in a fence -- the
    shape a doc explaining this very guard would write -- exempted everything after it from the
    retired-key sweep. An unclosed fence therefore stops any LATER heading being recognised at
    all, which fails toward scanning MORE than intended rather than less, so it cannot hide a
    retired key that was not already inside a genuine block.

    An UNTERMINATED block -- a BREAKING heading with no heading after it anywhere -- exempts to
    EOF. That is a decision, not an accident: an ATX section runs to the next heading or to the
    end of the document, so exempting to EOF is what the heading actually means, and the
    alternative would make the exemption depend on whether a later release section happens to
    exist below it. The exposure stays bounded by the three scoping rules above (CHANGELOG.md
    only, retired-key patterns only), and CLAUDE.md's release process invites the hand-editing
    that makes the shape reachable, so it gets its own row in
    `test_breaking_block_stripping_is_bounded` rather than being left to chance.
    """
    out, skipping, fence = [], False, None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if m and fence is None:
            # Opening: remember the character and length so only a matching closer ends it.
            fence = m.group(1)
        elif m and fence is not None:
            # Closing only if same char, at least as long, and nothing but whitespace after.
            if m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
        elif fence is None:
            if _is_breaking_heading(line):
                skipping = True
                continue
            if skipping and _ANY_HEADING.match(line):
                skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _scan_target(path, text):
    """What the retired-key sweep actually reads for `path`.

    The whole of #170's fix, in one named place. It was a conditional buried in the sweep's
    loop, and BOTH mutations that revert it -- returning `text` unconditionally, and dropping
    the `CHANGELOG.md` gate so every doc's BREAKING blocks go exempt -- survived the suite with
    everything green, because `main`'s CHANGELOG.md carries no BREAKING heading and so neither
    mutation changes anything observable against the live tree. Naming it is what lets
    `test_the_narrowing_reaches_changelog_md_and_no_other_doc` assert on it directly.
    """
    return _without_breaking_blocks(text) if path == "CHANGELOG.md" else text


def _retired_key_failures(docs):
    """`[(path, why, matched text)]` for every retired-key instruction in `(path, text)` pairs.

    The sweep's body, split out so a synthetic corpus can drive the REAL scan -- including its
    `_scan_target` call -- rather than only the helpers underneath it. Testing the pieces while
    leaving the call site unexercised is precisely how the two mutations above stayed green.
    """
    out = []
    for path, text in docs:
        scanned = _scan_target(path, text)
        for pattern, why, _sample in _RETIRED_CONFIG:
            hit = pattern.search(scanned)
            if hit:
                out.append((path, why, hit.group(0)))
    return out


def test_no_shipped_doc_instructs_a_retired_config_key():
    docs = _read_all()
    assert len(docs) >= 5
    assert "CHANGELOG.md" in {path for path, _ in docs}, (
        "CHANGELOG.md dropped out of the sweep's scope -- the narrowing above skips its BREAKING "
        "block only, and is worthless if the file stops being read at all"
    )
    failures = _retired_key_failures(docs)
    assert not failures, "\n".join(
        f"{path} instructs a retired config key -- {why} ({matched!r})"
        for path, why, matched in failures)


def test_the_narrowing_reaches_changelog_md_and_no_other_doc():
    """The narrowing must be REACHED by the sweep, and reached for CHANGELOG.md alone.

    One synthetic release block, scanned twice under two filenames. Under `CHANGELOG.md` the
    retired key it names must be exempt (that is #170); under `README.md` the identical text
    must still be caught, because a `### ⚠ BREAKING CHANGES` heading in any other shipped doc
    would otherwise hide a real instruction to set one.

    Asserted through `_retired_key_failures` -- the sweep's own body -- and through
    `_scan_target` directly, so neither the decision nor the call site can be reverted in
    silence. Both halves compare against `_RETIRED_CONFIG`'s real patterns rather than a
    hand-written regex, so a pattern added to that list is swept here too.
    """
    release_block = (
        "## [1.0.0](x) (2026-08-21)\n\n"
        "### ⚠ BREAKING CHANGES\n\n"
        "* **cv:** a `cv.name: \"\"` left by a half-finished migration still raises.\n\n"
        "### Features\n\n"
        "* something unrelated\n"
    )
    assert _retired_key_failures([("CHANGELOG.md", release_block)]) == [], (
        "CHANGELOG.md's BREAKING block is no longer exempt -- this is the #170 failure, which "
        "blocks the release PR")
    assert _retired_key_failures([("README.md", release_block)]), (
        "the same BREAKING block went exempt in README.md -- the narrowing must be scoped to "
        "CHANGELOG.md, or a real retired-key instruction hides under a BREAKING heading anywhere")

    # ...and the seam itself, both ways round, so the assertions above cannot be satisfied by a
    # `_scan_target` that ignores its `path` argument in either direction.
    assert _scan_target("CHANGELOG.md", release_block) != release_block, (
        "_scan_target returned CHANGELOG.md unchanged -- the narrowing never ran")
    assert _scan_target("README.md", release_block) == release_block, (
        "_scan_target stripped a non-CHANGELOG doc")


def test_breaking_block_stripping_is_bounded():
    """The narrowing must remove the BREAKING block and NOTHING else.

    Synthetic input, deliberately: `main`'s CHANGELOG.md carries no BREAKING heading, so a
    stripper that returned "" -- or that swallowed the rest of the file from the first heading
    on -- would make the sweep pass vacuously on the very file it was narrowed for, and nothing
    reading the live tree today would notice.
    """
    text = (
        "## [1.0.0](x) (2026-08-21)\n\n"
        "### BREAKING CHANGES\n\n"
        "* **cv:** a `cv.name: \"\"` left by a half-finished migration still raises.\n\n"
        "### Features\n\n"
        "* something unrelated\n\n"
        "## [0.1.0](y) (2026-08-06)\n\n"
        "Set cv.name: \"Ada Example\" in your config.\n"
    )
    stripped = _without_breaking_blocks(text)
    assert "half-finished migration" not in stripped, "the BREAKING block was not removed"
    assert "### Features" in stripped, "stripping ran past the block into the next section"
    assert "something unrelated" in stripped, "stripping swallowed a later section's body"
    assert "## [0.1.0](y)" in stripped, "stripping swallowed a later release entirely"
    # The load-bearing half: a retired key OUTSIDE any BREAKING block is still visible, so the
    # guard keeps catching a changelog that genuinely instructs one.
    assert 'cv.name: "Ada Example"' in stripped, (
        "a retired key outside the BREAKING block was stripped too -- the guard would no longer "
        "catch a changelog that actually instructs setting one"
    )
    assert _without_breaking_blocks("no headings here") == "no headings here"

    # A heading QUOTED inside a code fence is code, not structure. This is the shape a doc
    # explaining this very guard would write, and before fence tracking it exempted the whole
    # rest of the file from the retired-key sweep.
    quoted = '```\n### BREAKING CHANGES\n```\n\nSet cv.name: "Ada Example" in your config.\n'
    assert 'cv.name: "Ada Example"' in _without_breaking_blocks(quoted), (
        "a BREAKING heading quoted inside a code fence exempted the rest of the file")

    # A fence closes only on its OWN delimiter, per CommonMark: same character, at least as
    # long, nothing but whitespace after. Toggling on any ``` or ~~~ let a `~~~` line INSIDE a
    # ``` block close it, after which the heading below was read as structure again -- the hole
    # this row exists to keep shut (CodeRabbit, #171). Four spellings, one per closing rule.
    for label, doc in (
        ("a ~~~ line inside a ``` block",
         '```\n~~~\n### BREAKING CHANGES\n```\n\nSet cv.name: "Ada Example" in your config.\n'),
        ("a SHORTER closer",
         '````\n### BREAKING CHANGES\n```\n\nSet cv.name: "Ada Example" in your config.\n'),
        ("a closer carrying an info string",
         '```\n### BREAKING CHANGES\n``` python\n\nSet cv.name: "Ada Example" in your config.\n'),
        ("a ``` line inside a ~~~ block",
         '~~~\n```\n### BREAKING CHANGES\n~~~\n\nSet cv.name: "Ada Example" in your config.\n'),
    ):
        assert 'cv.name: "Ada Example"' in _without_breaking_blocks(doc), (
            f"{label} closed the fence, so the heading inside the code block exempted the rest"
        )

    # An UNCLOSED fence must not swallow the file either: it suppresses later headings, which
    # means less is exempted, never more.
    unclosed = '```\n### BREAKING CHANGES\n\nSet cv.name: "Ada Example" in your config.\n'
    assert 'cv.name: "Ada Example"' in _without_breaking_blocks(unclosed), (
        "an unclosed fence swallowed the rest of the file")

    # The mirror image: a fence INSIDE a real BREAKING block must not end the skip, because
    # fence state is not heading state. The block still ends at its own next heading.
    fenced_inside = (
        "### BREAKING CHANGES\n\n"
        '```yaml\ncv:\n  name: "Ada Example"\n```\n\n'
        "### Features\n\nkeep me\n"
    )
    stripped = _without_breaking_blocks(fenced_inside)
    assert "Ada Example" not in stripped, "a fence inside the block ended the skip early"
    assert "keep me" in stripped, "a fence inside the block ran the skip past its own section"

    # STATED DECISION: a BREAKING block that is the file's last heading exempts to EOF, which
    # is what an ATX section means. Every other row here supplies a following heading, so
    # nothing else in this file would notice if this behaviour changed. CLAUDE.md's release
    # process invites hand-editing this changelog, so the shape is reachable.
    to_eof = '### BREAKING CHANGES\n\n* a `cv.name: ""` left behind still raises.\n'
    assert "cv.name" not in _without_breaking_blocks(to_eof), (
        "a BREAKING block that is the file's last heading must exempt to EOF")

    # A BARE `###` is a valid empty ATX heading and must terminate a block. Requiring a space
    # or text after the hashes would silently run the exemption on to the next non-empty
    # heading instead.
    bare = '### BREAKING CHANGES\n\nSet cv.name: "x" here.\n\n###\n\nkeep me\n'
    assert "keep me" in _without_breaking_blocks(bare), (
        "a bare `###` is a valid empty ATX heading and must terminate a BREAKING block")

    # A LEVEL-1 heading terminates a block, even though `_ATX` refuses level 1 as a block
    # START -- see the H1 rows in test_only_an_exact_breaking_heading_is_stripped for why that
    # asymmetry is deliberate. `# Changelog` is the one H1 both changelogs actually contain.
    level_one = '### BREAKING CHANGES\n\nSet cv.name: "x" here.\n\n# Changelog\n\nkeep me\n'
    assert "keep me" in _without_breaking_blocks(level_one), (
        "a level-1 heading must terminate a BREAKING block")


def test_only_an_exact_breaking_heading_is_stripped():
    """A heading merely CONTAINING the phrase must not exempt its body.

    The first spelling of `_BREAKING_HEADING` was `.*BREAKING CHANGES.*`, which matched
    `### NOT BREAKING CHANGES` too -- so anything filed under such a heading would have been
    invisible to the retired-key sweep. A narrowing wide enough to hide what it narrows around
    is the failure this whole guard exists to avoid, reproduced one layer up.
    """
    for heading in ("### \u26a0 BREAKING CHANGES", "### BREAKING CHANGES",
                    "## BREAKING CHANGES ##", "###   BREAKING CHANGES",
                    # A hand-edit leaves trailing whitespace behind, which the final `.strip()`
                    # in `_is_breaking_heading` is there to absorb. Nothing else in this table
                    # needed that strip, so without this row it could be deleted unnoticed.
                    "### BREAKING CHANGES "):
        body = f"{heading}\n\nSet cv.name: \"Ada Example\" here.\n\n### Features\n\nkeep me\n"
        stripped = _without_breaking_blocks(body)
        assert "Ada Example" not in stripped, f"{heading!r} should exempt its body"
        assert "keep me" in stripped, f"{heading!r} stripped past its own section"

    # A closed-ATX suffix needs a SPACE before it, so `BREAKING CHANGES###` is literal text and
    # the heading's text is not exactly the phrase. `#*` admitted it (CodeRabbit round 4).
    #
    # `#BREAKING CHANGES` is refused by hash COUNT (one is below `_ATX`'s floor of two), so it
    # says nothing about the space rule; `##BREAKING CHANGES` has a valid count and no space,
    # which is what isolates the `[ \t]+` requirement.
    #
    # RULING on `# BREAKING CHANGES` (CodeRabbit CLI asked for level 1 to be a block START
    # too): level 1 stays refused. The only level-1 heading in either the hand-written or the
    # generated changelog is `# Changelog`, the document title -- verified, exactly one in each
    # -- so a level-1 BREAKING SECTION is not a shape either file uses, and after four rounds of
    # this pattern being too wide the correct bias is the smaller exempt surface. Terminating on
    # level 1 is a different question and stays permitted; see the level-one row in
    # test_breaking_block_stripping_is_bounded.
    for heading in ("### NOT BREAKING CHANGES", "## Some BREAKING CHANGES notes",
                    "### BREAKING CHANGES policy", "### BREAKING CHANGES###",
                    "### BREAKING CHANGES#", "#BREAKING CHANGES",
                    "##BREAKING CHANGES", "# BREAKING CHANGES"):
        body = f"{heading}\n\nSet cv.name: \"Ada Example\" here.\n"
        assert "Ada Example" in _without_breaking_blocks(body), (
            f"{heading!r} is not an exact BREAKING CHANGES heading and must NOT exempt its body"
        )

    # A heading marker must live on ONE physical line. `\s` matched newlines, so `###` alone
    # followed by a `BREAKING CHANGES` paragraph -- two separate blocks in Markdown, no heading
    # anywhere -- swallowed everything after it.
    #
    # These rows go through `_without_breaking_blocks`, which does `text.split("\n")` FIRST, so
    # no line reaching `_ATX` can contain a newline: what they actually pin is the SPLIT, not
    # `_ATX`'s `[ \t]` class. Measured -- reverting `_ATX` to the `\s+` spelling named above
    # leaves them all green. The class is pinned by the direct calls below instead.
    for malformed in ("###\nBREAKING CHANGES", "###\n\nBREAKING CHANGES", "##\n  BREAKING CHANGES"):
        body = f"{malformed}\n\nSet cv.name: \"Ada Example\" here.\n"
        assert "Ada Example" in _without_breaking_blocks(body), (
            f"{malformed!r} spans a newline and is not a heading -- it must NOT exempt its body"
        )
        # The same rule asked of the predicate that enforces it, with a string the line split
        # cannot flatten on its behalf.
        assert not _is_breaking_heading(malformed), (
            f"{malformed!r} spans a newline -- `_ATX` must not accept it as a heading")


# The NESTED-YAML half of the same sweep, closing what the comment above `_RETIRED_CONFIG`
# recorded as a deliberate gap (CodeRabbit, PR #161). The dotted patterns there match only
# `cv.name: <value>`; nobody writes a config that way. The shape a real config example takes
# is
#     cv:
#       name: "Ada Example"
# and `sluice.yaml.example`'s own commented convention (`# cv:` / `#   name: ...`) is the
# same shape behind a comment marker -- the one place the gap was already OPEN rather than
# dormant, because `tests/test_config_example.py`'s unknown-key sweep `continue`s on a block
# that YAML parses as a comment.
#
# The comment above ruled against widening the dotted REGEXES, and that ruling stands: a
# regex that tries to see block structure false-positives on unrelated indented content
# elsewhere on the page. This is not that. It is a two-state line scanner that knows which
# block it is inside, so `name:` under `cv:` is flagged and `name:` under anything else --
# or at the top level, or in prose -- is not. Being section-aware is what removes the
# false-positive risk the regex approach could not.
_RETIRED_UNDER_CV = ("name", "contact")
# A SINGLE leading `#` is a YAML comment marker and is stripped; `##` or more is a markdown
# heading and is not, because `## cv:` in a doc's prose is a section title, not a block
# opener -- treating it as one would then flag any indented `name:` further down the page.
_COMMENT_MARKER = re.compile(r"^(\s*)#(?!#)[ \t]?")
_KEY_LINE = re.compile(r"^(\s*)([a-z_]+)\s*:\s*(.*)$")


def _measure(line):
    """`(indent, key, value)` for a config-ish line, or `None` when it carries no
    structure.

    Indentation is measured on the text AFTER an optional YAML comment marker, which is
    what lets the scanner read `sluice.yaml.example`'s commented catalogue (`# cv:` /
    `#   name: ...`) with exactly the same block logic as an uncommented file. Measuring
    it on the raw line instead collapses that shape to a single indent level -- and that
    file is the one place this gap was already open rather than dormant, so the scanner
    missing it would leave the sweep looking clean while covering nothing new.
    """
    stripped = _COMMENT_MARKER.sub(r"\1", line)
    if not stripped.strip():
        return None
    m = _KEY_LINE.match(stripped)
    if not m:
        return None
    return len(m.group(1)), m.group(2), m.group(3).strip()


def _nested_cv_keys(text):
    """`[(lineno, key, value)]` for every retired key written UNDER a `cv:` block."""
    hits, cv_indent = [], None
    for n, line in enumerate(text.splitlines(), 1):
        measured = _measure(line)
        if measured is None:
            continue                       # blank, prose, or a markdown heading
        indent, key, value = measured
        if cv_indent is not None and indent <= cv_indent:
            cv_indent = None               # dedented back out of the block
        if key == "cv" and not value:
            cv_indent = indent
        elif cv_indent is not None and key in _RETIRED_UNDER_CV and value:
            hits.append((n, key, value))
    return hits


def test_no_shipped_doc_nests_a_retired_key_under_a_cv_block():
    checked = 0
    for path, text in _read_all():
        checked += 1
        hits = _nested_cv_keys(text)
        assert not hits, (
            f"{path} sets a retired key inside a `cv:` block -- identity moved to the vault's "
            f"Candidate Profile note (#133/#107) and a config setting either now RAISES at "
            f"load: {hits}")
    assert checked >= 5, "the nested-YAML sweep read almost nothing -- it is broken, not clean"


@pytest.mark.parametrize("label,sample,expected", [
    ("plain nested YAML", 'cv:\n  name: "Ada Example"\n', [(2, "name", '"Ada Example"')]),
    ("the commented catalogue shape", '# cv:\n#   contact: "x"\n', [(2, "contact", '"x"')]),
    ("indented inside a fence", '```yaml\ncv:\n    name: Ada\n```\n', [(3, "name", "Ada")]),
    # ...and the shapes it must NOT flag, which are what make it section-aware rather than
    # another regex. The last is the exact false positive the dotted patterns were narrowed
    # to avoid: prose that NAMES the retired key so a reader knows what to delete.
    ("a live cv: key", "cv:\n  renderer: template\n", []),
    ("name: under another block", "track:\n  name: x\n", []),
    ("a top-level name:", "name: x\n", []),
    ("dedented back out", "cv:\n  renderer: template\nother:\n  name: x\n", []),
    ("migration prose", "Remove cv.name and cv.contact from your config.\n", []),
    # A markdown heading is not a block opener. `##`+ is deliberately NOT stripped as a
    # comment marker for exactly this row: strip it and a section titled `cv:` would open
    # a block that never closes, flagging any indented `name:` anywhere below it.
    ("a markdown heading named cv:", "## cv:\n\nSome prose.\n\n  name: x\n", []),
])
def test_the_nested_cv_scanner_sees_every_shape_and_only_those(label, sample, expected):
    """Positive AND negative controls in one table, because this scanner's whole claim over
    a regex is that it distinguishes the two. A sweep asserting only `hits == []` across the
    real docs cannot tell "no doc does this" from "the scanner matches nothing at all" --
    `all([])` is `True` -- so the positive rows are what stop it from being vacuous, and the
    negative rows are what stop it from being the false-positive-prone regex it replaced.
    """
    assert _nested_cv_keys(sample) == expected, f"{label}: {_nested_cv_keys(sample)!r}"


@pytest.mark.parametrize("pattern,why,sample", _RETIRED_CONFIG,
                         ids=[why for _p, why, _s in _RETIRED_CONFIG])
def test_every_retired_config_pattern_matches_its_own_sample(pattern, why, sample):
    """POSITIVE CONTROL, one row per pattern. The single-sample version of this test asserted
    only `hits` non-empty over a sample that just ONE of the seven patterns matched -- so the
    other six, including both patterns added for cv.name/cv.contact, could match nothing at all,
    forever, with the suite green. Measured before this fix: exactly 1 of 7 patterns matched the
    old single sample. Parametrizing over every (pattern, sample) pair the way this list is
    actually consumed means a pattern that stops matching its own motivating shape fails on its
    own row rather than hiding behind six others that still pass."""
    assert pattern.search(sample), f"{why} -- pattern no longer matches its own positive sample"


def test_the_migration_removal_sentence_does_not_trip_the_retired_config_sweep():
    """NEGATIVE CONTROL for the two narrowed cv.name/cv.contact patterns. Their whole reason for
    being colon-plus-value rather than a bare key name is so migration prose that NAMES the
    retired keys (to tell a reader what to remove) is not itself flagged as an instruction to
    set them. This guards the WIDENING direction only: `assert hits == []` can only catch a
    pattern that started matching MORE than it should, so a regression back toward the bare-key
    shape (or any other widening) would start flagging this legitimate sentence and fail here.
    It says nothing about the opposite failure -- a pattern narrowed so far it stops matching
    the real defect shape too -- which is what the parametrized positive control above this one
    exists to catch instead, one row per pattern."""
    sample = "Remove cv.name and cv.contact from your config; identity now lives in the vault."
    hits = [why for pattern, why, _sample in _RETIRED_CONFIG if pattern.search(sample)]
    assert hits == [], f"legitimate migration prose tripped the retired-config sweep: {hits}"


# `sluice[extra]` targets a real, unrelated, dormant PyPI package (a zfs-snapshot tool, last
# released 2015) now that this project's distribution is `job-sluice` -- extras attach to the
# DISTRIBUTION name, not the import package. Matches an unquoted form too (`pip install
# sluice[completion]`), not only a quoted one -- an earlier version of this pattern required a
# leading quote and so missed the bare form entirely, caught before it shipped by a prior
# review of the PR that added this file. The negative lookbehind is the part that actually
# matters: without it, this pattern also matches INSIDE the correct spelling, since "sluice["
# is a literal substring of "job-sluice[" -- a naive fix (`['"]?sluice\[`) would have flagged
# every correct `job-sluice[render]` reference as if it were the bug it exists to catch.
_WRONG_DISTRIBUTION = re.compile(r"(?<!job-)\bsluice\[")


def test_no_shipped_doc_installs_the_wrong_pypi_distribution():
    """Every from-source install in these docs uses `pip install -e '.[extra]'` instead, which
    never touches an index by name at all; a doc naming a PyPI release directly must say
    `job-sluice[extra]`, never bare `sluice[extra]`.
    """
    checked = 0
    for path, text in _read_all():
        checked += 1
        hit = _WRONG_DISTRIBUTION.search(text)
        assert not hit, (
            f"{path} names the wrong PyPI distribution: {hit.group(0)!r} -- "
            f"should be job-sluice[...], or pip install -e '.[...]' for a from-source install")
    assert checked >= 5


def test_no_shipped_source_string_installs_the_wrong_pypi_distribution():
    """The same defect, swept over the SHIPPED PACKAGE rather than the docs tree.

    This is the half the doc-only sweep above cannot see, and the actual live incident: a
    `doctor` DEGRADED message, two renderers' `RenderError` text, and the `init` wizard's
    renderer hint all instructed `pip install 'sluice[render]'`/`'sluice[google]'` -- read by a
    real user at the exact moment they are debugging a broken install, not merely documentation
    prose. Found independently by two reviewers on the PR that added this file; the doc-only
    sweep's own docstring claimed broader coverage than it had, which is the failure mode this
    second, source-scoped sweep exists to close.
    """
    checked = 0
    for path in sorted(glob.glob("sluice/**/*.py", recursive=True)):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        checked += 1
        hit = _WRONG_DISTRIBUTION.search(text)
        assert not hit, (
            f"{path} names the wrong PyPI distribution: {hit.group(0)!r} -- "
            f"should be job-sluice[...]")
    assert checked >= 50, f"the sweep read only {checked} files under sluice/"


def test_the_wrong_distribution_pattern_is_falsified_by_both_forms_and_spares_the_right_one():
    """POSITIVE + NEGATIVE CONTROL. The positive half is what a prior review caught: an
    earlier version of `_WRONG_DISTRIBUTION` required a leading quote and so missed the bare
    `pip install sluice[completion]` form used nowhere in this repo today but not actually
    impossible in a future edit. The negative half is what a naive fix for THAT gap would have
    broken: `['"]?sluice\\[` (quote now optional, nothing else changed) matches inside the
    correct spelling too, since "sluice[" is a literal substring of "job-sluice[" -- so a
    pattern change that silences one false negative can silently introduce a false positive
    against the very text this project wants everywhere. Both properties are asserted together
    because a pattern that is only ever checked against one of the two shapes is exactly how the
    original quote-requiring version shipped in the first place.
    """
    assert _WRONG_DISTRIBUTION.search("pip install 'sluice[render]'")
    assert _WRONG_DISTRIBUTION.search("pip install sluice[completion]")  # the unquoted form
    assert not _WRONG_DISTRIBUTION.search("pip install 'job-sluice[render]'")
    assert not _WRONG_DISTRIBUTION.search("pip install job-sluice[completion]")


# A group name immediately followed by a flag, with no subcommand word between them, reads as
# a copy-pasteable command but isn't one: `job-sluice triage --no-llm` skips triage's required
# `run` subcommand and fails at the CLI. Needs the literal `job-sluice` prefix -- same reason as
# `_INVOCATION` above -- so this can't fire on the bare English word "leads", which appears
# constantly in this repo's own prose about job leads.
#
# CodeRabbit's review of PR #103 actually found this shape WITHOUT the `job-sluice` prefix --
# CHANGELOG.md and docs/TROUBLESHOOTING.md each listed bare `` `triage --no-llm` `` in a
# comma-separated list of offline commands (fixed by hand in that same PR, to `triage run
# --no-llm`). Matching that unprefixed shape would also match `` `cv --lead` `` in
# docs/ARCHITECTURE.md -- informal shorthand for the `Sluice.compose_cv` facade method's
# `--lead` argument in narrative prose about a historical bug fix, not a claim that `cv --lead`
# is runnable on its own -- so this sweep is deliberately narrower than the shipped defect and
# does NOT re-check the two hand-fixed lines. What it protects going forward is the more
# dangerous variant: an instruction actually prefixed `job-sluice`, which a reader would copy
# verbatim into a shell.
_GROUP_FLAG_WITHOUT_SUBCOMMAND = re.compile(r"\bjob-sluice\s+([a-z][a-z-]*)\s+(--[a-z][a-z-]*)")


def test_no_documented_invocation_skips_a_required_subcommand():
    """The converse gap `test_every_documented_command_claim_is_real` leaves open: that test
    only checks a claimed SUBCOMMAND is real, and silently skips every claim where `sub is None`
    -- which is exactly the shape of `job-sluice triage --no-llm`, a claim with no subcommand at
    all. A leaf group (health/init/doctor) takes no subcommand, so `job-sluice doctor --offline`
    is genuinely valid and must not trip this. See `_GROUP_FLAG_WITHOUT_SUBCOMMAND` above for
    what this sweep does and does not cover.
    """
    tree = _command_tree()
    checked = 0
    for path, text in _read_all():
        checked += 1
        for m in _GROUP_FLAG_WITHOUT_SUBCOMMAND.finditer(text):
            group, flag = m.group(1), m.group(2)
            if group not in tree or tree[group] is None:
                continue  # not a real group, or a leaf group that takes no subcommand
            assert False, (
                f"{path} claims `job-sluice {group} {flag}`, which skips {group}'s required "
                f"subcommand ({', '.join(tree[group])}) and fails at the CLI")
    assert checked >= 5, f"the sweep read only {checked} files"  # SCOPE, not just non-empty


def test_the_missing_subcommand_pattern_is_falsified_by_a_prefixed_form_and_spares_valid_forms():
    """POSITIVE + NEGATIVE CONTROL for the pattern's own shape (not the real shipped defect,
    which lacked the `job-sluice` prefix this sweep requires -- see the comment above
    `_GROUP_FLAG_WITHOUT_SUBCOMMAND`). The positive half is the same defect, prefixed the way a
    copy-pasteable instruction would be; the negative half proves the corrected form and a leaf
    group's genuinely bare flag both survive -- the corrected form via the regex itself (a
    subcommand word now sits between group and flag, so the flag no longer immediately follows
    the group), the leaf-group form via the `tree[group] is None` exemption in the sweep above,
    which this re-checks directly so a deleted exemption cannot pass unnoticed.
    """
    tree = _command_tree()
    hit = _GROUP_FLAG_WITHOUT_SUBCOMMAND.search("job-sluice triage --no-llm")
    assert hit and hit.group(1) in tree and tree[hit.group(1)] is not None

    assert not _GROUP_FLAG_WITHOUT_SUBCOMMAND.search("job-sluice triage run --no-llm")

    leaf_hit = _GROUP_FLAG_WITHOUT_SUBCOMMAND.search("job-sluice doctor --offline")
    assert leaf_hit and tree[leaf_hit.group(1)] is None


# ── the backend-credential table in docs/INSTALL.md (#104 PR 7) ──────────────────────────────
#
# WHY THIS EXISTS. `docs/INSTALL.md`'s "Backend credentials" section names one environment
# variable per provider, and `docs/CONFIGURATION.md` names them again. That is a second copy of a
# mapping the code owns (`sluice/core/app.py`'s `_PROVIDER_ENV`), and a review flagged it as the
# same duplication class this branch had already fixed once for the PDF prerequisites.
#
# Linking instead of duplicating was the suggested remedy, and it is the weaker one: a link stops
# the copy growing but does nothing about the copy that already exists, which drifts the day a
# provider is added or an env var renamed. This repo's own rule is to REMOVE the drift surface --
# derive it, or assert it beside the prose -- so the table is pinned to the code instead.
#
# `claude-max` is deliberately absent from `_PROVIDER_ENV` (a flat-rate CLI shell-out with no
# credentials to resolve, per `_provider_creds`), and the doc says exactly that, so the comparison
# is against the KEYED providers only.
# Relative, matching this module's own convention (`glob.glob("docs/*.md")` above) rather than
# introducing a second path idiom in one file.
_INSTALL_MD = "docs/INSTALL.md"
_CREDS_ROW = re.compile(r"^\|\s*`(?P<provider>[\w-]+)`\s*\|\s*(?P<needs>.+?)\s*\|\s*$", re.M)


def _install_credential_rows():
    """{provider: needs-cell} from INSTALL.md's Backend credentials table."""
    with open(_INSTALL_MD, encoding="utf-8") as f:
        text = f.read()
    start = text.index("## Backend credentials")
    end = text.index("##", start + 3)
    return {m.group("provider"): m.group("needs")
            for m in _CREDS_ROW.finditer(text[start:end])}


def test_the_install_guide_credential_table_matches_the_real_provider_env_map():
    """Every keyed provider appears with its real variable, and no invented provider appears.

    BOTH directions, because each fails differently: a provider missing from the doc leaves a
    user unable to configure it, while a provider named in the doc but absent from the code sends
    them to set a variable nothing reads.
    """
    from sluice.core.app import _PROVIDER_ENV

    rows = _install_credential_rows()
    assert rows, "the Backend credentials table was not found or did not parse"
    assert "claude-max" in rows, "the keyless provider row vanished from the table"
    assert "no API key" in rows["claude-max"], rows["claude-max"]

    documented = set(rows) - {"claude-max"}
    assert documented == set(_PROVIDER_ENV), (
        f"docs/INSTALL.md documents {sorted(documented)} but sluice/core/app.py's _PROVIDER_ENV "
        f"has {sorted(_PROVIDER_ENV)}")
    for provider, (key_var, _base_url) in _PROVIDER_ENV.items():
        assert key_var in rows[provider], (
            f"the {provider} row does not name {key_var}: {rows[provider]!r}")


# ── docs/INSTALL.md's channel coverage (#104 PR 7) ───────────────────────────────────────────
#
# README's channel table is the SINGLE place this repo states which channels exist, and
# `tests/test_release_publish_wiring.py` already pins it against the release workflow's job
# roster in both directions. Nothing connected that to the install guide, so a channel could ship
# -- job, table row, published artefact -- with no instructions telling anyone how to install it.
#
# A review suggested asserting that INSTALL's channel names EQUAL README's rows. They do not, and
# should not: README enumerates PUBLISHING channels while INSTALL enumerates INSTALL METHODS, and
# uv, pipx and pip are three methods against the one PyPI channel. `From source` is a method with
# no published channel behind it at all. Asserting a false equality would have forced one of the
# two documents to stop saying what it means, so the mapping is declared instead -- and asserted
# in both directions, so it cannot quietly go stale either.
_CHANNEL_TO_INSTALL_SECTIONS = {
    "PyPI": ("uv", "pipx", "pip"),
    "Docker": ("Docker",),
    "deb / rpm": ("deb / rpm",),
    "Homebrew": ("Homebrew (macOS)",),
}

_INSTALL_H2 = re.compile(r"^## (?P<title>.+?)\s*$", re.M)
_README_CHANNEL_ROW = re.compile(r"^\|\s*(?P<channel>[^|]+?)\s*\|\s*(?:shipped|planned)\s*\|", re.M)


def _readme_channels():
    with open("README.md", encoding="utf-8") as f:
        text = f.read()
    marker = "<!-- channel-status -->"
    return {m.group("channel") for m in _README_CHANNEL_ROW.finditer(text[text.index(marker):])}


def _install_sections():
    with open(_INSTALL_MD, encoding="utf-8") as f:
        return {m.group("title") for m in _INSTALL_H2.finditer(f.read())}


def test_every_published_channel_has_install_instructions():
    """A channel can ship -- job, README row, real artefact -- with nobody told how to install it.

    Both directions. A README channel absent from the mapping means a channel shipped and this
    guard was never taught about it; a mapped section absent from INSTALL means the instructions
    were renamed or deleted out from under a live channel.
    """
    channels = _readme_channels()
    assert channels, "README's channel table did not parse -- the sweep is vacuous"
    assert channels == set(_CHANNEL_TO_INSTALL_SECTIONS), (
        f"README lists {sorted(channels)} but this guard maps "
        f"{sorted(_CHANNEL_TO_INSTALL_SECTIONS)}. A new channel needs a docs/INSTALL.md section "
        f"and an entry here; a removed one needs both taken out.")

    sections = _install_sections()
    for channel, expected in _CHANNEL_TO_INSTALL_SECTIONS.items():
        missing = [s for s in expected if s not in sections]
        assert not missing, f"docs/INSTALL.md has no section {missing} for the {channel} channel"


def test_the_install_guide_enumerates_the_same_methods_in_both_of_its_tables():
    """Upgrading and Pinning are two lists of the same thing, maintained independently.

    Adding a method to one and not the other is exactly the drift a reader cannot see: each table
    looks complete on its own. Compared as SETS, since the two deliberately differ in order.
    """
    with open(_INSTALL_MD, encoding="utf-8") as f:
        text = f.read()

    def rows(heading, nxt):
        block = text[text.index(heading):text.index(nxt)]
        return {m.group(1).strip() for m in re.finditer(r"^\|\s*([^|]+?)\s*\|", block, re.M)
                if m.group(1).strip() not in ("Channel", "---")}

    upgrading = rows("## Upgrading", "## Pinning an older version")
    pinning = rows("## Pinning an older version", "## Checking the install")
    assert len(upgrading) >= 6, f"the Upgrading table did not parse: {upgrading}"
    assert upgrading == pinning, (
        f"Upgrading and Pinning disagree: only in Upgrading {sorted(upgrading - pinning)}, "
        f"only in Pinning {sorted(pinning - upgrading)}")


# DERIVED, not hand-listed: README.md plus every `docs/**/*.md` outside the excluded trees. A
# hand-list was the first attempt and was wrong within one revision -- it named two guides that
# ship no paste-in shell at all (`docs/CONFIGURATION.md` has zero fences; `docs/USAGE.md`'s are
# output-format specimens), which the per-file scope assertion below caught immediately. The
# deeper problem is the other direction: a hand-list silently fails to cover a bash block added
# to a guide nobody remembered to name, which is this repo's standing lesson about enumerating
# rather than listing.
#
# `docs/superpowers/` is excluded because it holds historical design documents (CLAUDE.md: "not
# maintained, and the code wins on any disagreement") whose blocks are transcripts of
# verification runs rather than instructions to a reader -- three legitimately use `exit`, and
# sweeping them would make this guard permanently red for no reader's benefit.
_EXCLUDED_DOC_TREES = ("docs/superpowers/",)


def _paste_in_guides():
    """Every user-facing markdown guide whose shell blocks a reader is meant to paste."""
    paths = ["README.md"] + sorted(glob.glob("docs/**/*.md", recursive=True))
    return [p for p in paths
            if not any(p.startswith(tree) for tree in _EXCLUDED_DOC_TREES)]

# `exit` as a STATEMENT. The prefix set is the POSIX COMMAND-SEPARATOR set plus the reserved
# words that introduce a command position -- taken as a whole rather than assembled one
# reviewer-found case at a time, which is how the CV parser's grammar in this repo accumulated
# its long tail of gate-clean refusals. Two rounds of review each supplied exactly one more
# spelling before it was written down properly:
#
#   separators   ;  ;;  &  &&  |  ||  (  )  {  and start-of-line
#   reserved     then  else  elif  do
#
# `{` is in and `}` is NOT, which is the asymmetry to keep: `{` OPENS a command group so a
# command follows it, while `}` closes one and must itself be followed by a separator -- `} exit`
# is not valid shell. `}` was in this set for one revision purely because the two brace
# characters read as a pair; mutation testing exposed it, since deleting it changed nothing.
# An alternative nothing can exercise is dead weight a mutant cannot kill.
#
# `)` is the one that looks wrong until you write a `case`: `value) exit 1;;` puts `exit` in
# command position after a close paren, and a set holding only `(` walks straight past it.
# `; then exit` was the previous round's miss for the same reason -- no delimiter sits
# immediately before `exit`, only the keyword. Leading whitespace is allowed (`^\s*`), or an
# indented `exit` inside an `if` body slips through.
#
# Still NOT matched, and each is pinned in the synthetic corpus below: `--exit-code`, `my_exit`,
# `get_exit_code`, `sys.exit(...)`, `EXITCODE=$?`, and the word in prose -- none of which put a
# bare `exit` in command position.
_BARE_EXIT = re.compile(
    r"(?:^\s*|[;&|(){]\s*|(?:^|[\s;&|(){])(?:then|else|elif|do)\s+)exit\b", re.M)

# Fence matching is deliberately permissive about the DELIMITER and strict about the LANGUAGE: a
# spelling the matcher cannot see contributes zero blocks SILENTLY, and a sweep that reads
# nothing passes every assertion made about it.
#
# Scanned line-by-line rather than by one regex, because CommonMark's closing rule is "the same
# character, at least as long as the opening run" -- a length COMPARISON, which a backreference
# cannot express. The regex version this replaced used `(?P=fence)` and so required an exact
# match: measured, it silently missed ```` ```bash ... ```` ```` (a longer close, which is valid)
# while also failing to reject a shorter one. Getting that backwards is how a whole block leaves
# the corpus with nothing going red.
_FENCE_LANGS = ("bash", "sh", "shell", "console")
_FENCE_OPEN = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[A-Za-z0-9_+-]*)[ \t]*$")


def _shell_blocks(text):
    """Bodies of every fenced block whose info string names a shell language.

    Up to three spaces of indentation, backticks or tildes, runs longer than three, trailing
    spaces after the info string, and CRLF are all legal and all appear in the wild; each one
    used to drop a block from the corpus without a word.
    """
    blocks, lines, i = [], text.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        fence, lang = m.group("fence"), m.group("lang").lower()
        char, need = fence[0], len(fence)
        # The closing fence: same character, at least as long, nothing else on the line.
        close = re.compile(r"^[ \t]{0,3}" + re.escape(char) + r"{%d,}[ \t]*$" % need)
        j = i + 1
        while j < len(lines) and not close.match(lines[j]):
            j += 1
        if lang in _FENCE_LANGS:
            blocks.append("\n".join(lines[i + 1:j]))
        i = j + 1  # resume AFTER the closing fence, so a nested fence cannot reopen
    return blocks


def _shell_killing_lines(scanned):
    """(rel, line) for every line in `scanned` that would exit the reader's interactive shell.

    At MODULE scope so a synthetic corpus can be run through the real filter. Inline in the test
    it could only ever see the live guides -- which are clean, so neutralising the condition
    changed nothing and the mutant survived: `offenders == []` is the SUCCESS case for a negative
    sweep, and no assertion over an empty list can tell "nothing is wrong" from "nothing was
    examined". `test_the_shell_killing_filter_actually_flags_a_bad_block` is what closes that.

    Skips a WHOLE-LINE comment, and only that. Caught on this guard's first run: the comment
    added beside the fix -- "`if`, NOT `|| exit`" -- is itself an `|| exit` in statement position,
    so a filter that reads every line flags the note explaining why the hazard was removed.
    Trailing comments are deliberately NOT stripped: `#` inside a quoted string is not a comment,
    and a splitter that does not parse the shell would cut a real command in half -- the
    stop-patching-and-parse case this repo has already been bitten by. It costs nothing here,
    because `_BARE_EXIT` only matches `exit` in statement position, and prose ("use exit 1 to
    stop") does not put it there.
    """
    return [
        (rel, line.strip())
        for rel, blocks in scanned.items()
        for block in blocks
        for line in block.splitlines()
        if not line.lstrip().startswith("#") and _BARE_EXIT.search(line)
    ]


def test_the_shell_killing_filter_actually_flags_a_bad_block():
    """The sweep above passes when the guides are clean AND when it examines nothing.

    So the filter is driven here over a corpus chosen to contain the exact shapes that matter,
    rather than trusted because the real sweep is green. Measured: with this absent, replacing
    the filter's condition with `False` left the whole file green.
    """
    scanned = {"synthetic.md": ["""curl -fsSL -o /tmp/x https://example.invalid/x
CLAUDE_BIN=$(command -v claude) || { echo "no claude on PATH"; exit 1; }
if [ -z "$CLAUDE_BIN" ]; then exit 1; fi
for f in a b; do exit 1; done
if x; then :; else exit 1; fi
(cd /tmp && missing) || exit 1
mkdir -p /tmp/x && exit 1
( exit 1 )
{ exit 1; }
case "$x" in value) exit 1;; esac
case "$x" in a) :;; *) exit 1;; esac
if a; then :; elif b; then exit 1; fi
if a; then :; elif exit 1; then :; fi
until x; do exit 1; done
  exit 2
exit
# `if`, NOT `|| exit` -- a whole-line comment must NOT be flagged
echo "use exit 1 to stop"  # prose after code: `exit` is not in statement position
foo --exit-code
my_exit 1
get_exit_code
sys.exit(1)
EXITCODE=$?
"""]}
    flagged = [line for _rel, line in _shell_killing_lines(scanned)]
    assert flagged == [
        'CLAUDE_BIN=$(command -v claude) || { echo "no claude on PATH"; exit 1; }',
        'if [ -z "$CLAUDE_BIN" ]; then exit 1; fi',
        "for f in a b; do exit 1; done",
        "if x; then :; else exit 1; fi",
        "(cd /tmp && missing) || exit 1",
        # `&&` DIRECTLY before `exit`. The line above contains `&&` but not in that position, so
        # it is caught by `||` instead and deleting `&` from the pattern changed nothing.
        "mkdir -p /tmp/x && exit 1",
        # `(` and `{` OPEN a command position, so `exit` can follow either directly. Both needed
        # their own line: every other bracket case in this corpus is also caught by a `;` or
        # `||` elsewhere on the line, so deleting `(` or `{` from the pattern changed nothing.
        "( exit 1 )",
        "{ exit 1; }",
        # `)` in command position -- a `case` clause. Missed for two rounds by a set that held
        # only `(`, and the reason the prefix set is now written as a whole rather than grown.
        'case "$x" in value) exit 1;; esac',
        'case "$x" in a) :;; *) exit 1;; esac',
        "if a; then :; elif b; then exit 1; fi",
        # `elif` in the set is only load-bearing if something puts `exit` DIRECTLY after it --
        # the line above exercises `then`, not `elif`, and with only that one present, deleting
        # `elif` from the pattern left the suite green. An unexercised alternative is dead weight
        # a mutant cannot kill, so the contrived-but-valid shape is pinned deliberately.
        "if a; then :; elif exit 1; then :; fi",
        "until x; do exit 1; done",
        "exit 2",
        "exit",
    ], f"the filter flagged {flagged!r}"


def test_the_fence_scanner_reads_the_shell_fence_spellings_that_occur_in_practice():
    """A fence spelling the scanner cannot see contributes zero blocks, silently.

    That is the dangerous direction for a negative sweep: the guide still appears in the corpus,
    it simply has nothing in it, and every assertion over its (empty) offender list passes. Every
    variant below is legal CommonMark, and each was UNMATCHED by the first version of this code,
    which required exactly three backticks at column zero followed immediately by a newline.

    The last two are the reason this is a scanner and not a regex: CommonMark's closing rule is a
    length COMPARISON (same character, at least as long as the opening), which a backreference
    cannot express. The intermediate `(?P=fence)` version got BOTH wrong -- it rejected a valid
    longer close and accepted an invalid shorter one -- and neither shows up as a failure, only
    as a block quietly leaving the corpus.
    """
    variants = {
        "plain": "```bash\nexit 1\n```\n",
        "indented (CommonMark allows up to three spaces)": "   ```bash\n   exit 1\n   ```\n",
        "tilde delimiters": "~~~bash\nexit 1\n~~~\n",
        "CRLF line endings": "```bash\r\nexit 1\r\n```\r\n",
        "trailing space after the info string": "```bash \nexit 1\n```\n",
        "a fence run longer than three": "````bash\nexit 1\n````\n",
        "console": "```console\nexit 1\n```\n",
        "sh": "```sh\nexit 1\n```\n",
        "closing fence LONGER than the opening (a valid close)": "```bash\nexit 1\n````\n",
    }
    for name, text in variants.items():
        assert _shell_blocks(text), f"{name}: contributes nothing to the sweep"
        assert [line for _r, line in _shell_killing_lines({name: _shell_blocks(text)})] == \
            ["exit 1"], f"{name}: matched a fence but the body did not reach the filter"

    # The language filter still holds: a python block is not a shell block, even though `exit(1)`
    # appears in it. Widening the sweep to every fence would flag it.
    assert not _shell_blocks("```python\nexit(1)\n```\n")

    # The two closing-rule cases, asserted on the exact BODY rather than merely "non-empty".
    # Both mutants survived a non-empty check: an exact-length close simply runs to end-of-file,
    # which still yields a block containing `exit 1`, so the sweep looks unaffected while the
    # block boundary is wrong.
    assert _shell_blocks("```bash\nexit 1\n````\n") == ["exit 1"], (
        "a closing fence LONGER than the opening is a valid close; the body must stop there "
        "rather than running to the end of the document")
    # A SHORTER run does not close the block, so the inner fence stays part of the body.
    assert _shell_blocks("````bash\n```\nexit 1\n````\n") == ["```\nexit 1"]
    # ...and scanning resumes AFTER the close, so an inner fence is never reopened as a block of
    # its own. With the resume point wrong this returns the inner block a second time.
    assert _shell_blocks("````bash\n```bash\nexit 1\n````\n") == ["```bash\nexit 1"], (
        "an inner fence was reopened as a separate block; scanning must resume after the close")


def test_no_paste_in_snippet_can_close_the_readers_shell():
    """`exit` in a block the docs tell you to paste kills the terminal instead of the setup.

    Found by review on `docs/INSTALL.md`'s wrapper-install block, which guarded a missing
    `claude` with `|| { echo ...; exit 1; }`. In a script that is correct; pasted into an
    interactive shell -- which is exactly what the surrounding prose instructs -- it closes the
    window, taking any unsaved session with it, and the diagnostic it just printed scrolls away
    with it. The fix there was `if [ -z "$CLAUDE_BIN" ]; then ... else ... fi`.

    Swept rather than spot-fixed because the next snippet added to any of these guides has the
    same hazard and nothing else in the suite reads a fenced block for runnability.
    """
    # ASSERT ON THE SCOPE FIRST. A fence regex that stops matching (a new info-string, a change
    # of fence character) yields an empty corpus, and a sweep over nothing passes every
    # assertion made about it -- the failure mode this repo has hit repeatedly.
    # Relative paths, matching this module's own convention (`glob.glob("docs/*.md")` above).
    guides = _paste_in_guides()
    scanned = {rel: _shell_blocks(pathlib.Path(rel).read_text(encoding="utf-8"))
               for rel in guides}
    # A per-file "must be non-empty" is wrong once the corpus is derived -- plenty of guides
    # legitimately ship no shell at all. What must hold instead is that the MATCHER works and the
    # EXCLUSION is real, so pin both directly.
    assert "docs/INSTALL.md" in guides, (
        f"the guide this sweep was written for is not in the derived corpus: {guides}")
    assert scanned["docs/INSTALL.md"], (
        "docs/INSTALL.md contributed no shell blocks; the fence matcher has stopped matching and "
        "this sweep would pass while reading nothing")
    total = sum(len(v) for v in scanned.values())
    assert total >= 30, (
        f"only {total} shell blocks across {len(guides)} guides; expected the matcher to find "
        f"far more: { {k: len(v) for k, v in scanned.items() if v} }")
    # The exclusion must actually exclude something. An `_EXCLUDED_DOC_TREES` that matched no
    # real path would be inert, and nothing else here would notice -- the guard would simply be
    # red for a reason the message does not explain.
    assert glob.glob("docs/superpowers/**/*.md", recursive=True), (
        "docs/superpowers/ matched no files, so _EXCLUDED_DOC_TREES is inert; if that tree was "
        "removed, drop the exclusion rather than leaving a rule that does nothing")

    offenders = _shell_killing_lines(scanned)
    assert not offenders, (
        "a shell block in a paste-in guide would close the reader's interactive shell; use an "
        "`if`/`else` so only the setup stops:\n" +
        "\n".join(f"  {rel}: {line}" for rel, line in offenders))


def test_install_guide_and_compose_agree_on_the_image_tag_variable():
    """`docs/INSTALL.md` states what `docker-compose.yml` defaults the image tag to. Pin it.

    The wrapper-install block derives `REF` from `JOB_SLUICE_TAG` and explains that this is safe
    BECAUSE compose reads the same variable with a `latest` default -- so the wrapper and the
    image move together. That is a claim about a different file, made in prose, which is this
    repo's most persistent source of stale documentation. If either default changes, the
    reasoning printed for the reader becomes wrong while both files stay individually valid.
    """
    compose = pathlib.Path("docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"image:\s*ghcr\.io/\S+/job-sluice:\$\{JOB_SLUICE_TAG:-(?P<default>[\w.-]+)\}",
                  compose)
    assert m, ("could not find the job-sluice image line in docker-compose.yml; if the image or "
               "its tag variable was renamed, docs/INSTALL.md's REF reasoning needs updating too")
    compose_default = m.group("default")

    install = pathlib.Path("docs/INSTALL.md").read_text(encoding="utf-8")
    ref = re.search(r'REF="\$\{JOB_SLUICE_TAG:-(?P<default>[\w.-]+)\}"', install)
    assert ref, "could not find the REF assignment in docs/INSTALL.md"

    # The two defaults are not the same STRING -- an image tag and a git ref are different
    # namespaces -- so what is pinned is the pairing the prose actually asserts: compose's
    # default is `latest`, and the guide says `main` goes with it.
    assert compose_default == "latest", (
        f"docker-compose.yml now defaults the image tag to {compose_default!r}; docs/INSTALL.md "
        f"tells the reader it is `latest` and pairs it with the `main` wrapper ref")
    assert ref.group("default") == "main", (
        f"docs/INSTALL.md now defaults REF to {ref.group('default')!r}, which no longer matches "
        f"the `:latest` image its own prose pairs with `main`")
    assert "${JOB_SLUICE_TAG:-latest}" in install, (
        "docs/INSTALL.md quotes compose's image line to justify the pairing; that quotation is "
        "gone, so the reasoning shown to the reader is no longer anchored to anything")


def _parser_flags(group: str, sub: str) -> set:
    """Every long option `job-sluice <group> <sub>` really accepts, from the live parser.

    Walked through argparse's own `_SubParsersAction`/`.choices`, the same private-API shape
    `_command_tree` above uses, so it cannot drift from `--help`. `-h/--help` is dropped: it
    is argparse's, not the command's, and no doc heading lists it.
    """
    parser = _build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    group_parser = top.choices[group]
    group_sp = next(a for a in group_parser._actions
                    if isinstance(a, argparse._SubParsersAction))
    return {opt for action in group_sp.choices[sub]._actions
            for opt in action.option_strings
            if opt.startswith("--") and opt != "--help"}


def _documented_flags(usage: str, group: str, sub: str) -> set:
    """The long options USAGE.md's heading for `<group> <sub>` shows the reader."""
    for line in usage.splitlines():
        if line.startswith(f"### `job-sluice {group} {sub} "):
            return set(re.findall(r"--[a-z][a-z-]*", line))
    return set()


@pytest.mark.parametrize("kind", sorted(EVIDENCE_KINDS))
def test_every_evidence_add_flag_is_documented(kind):
    """`cli.py` generates one `--<field>` flag per `EvidenceKind.fields` entry, so ADDING A
    FIELD silently adds a user-visible flag. `test_every_real_command_is_documented_in_usage_md`
    above compares COMMANDS and never looks at flags, so that addition documents itself
    nowhere -- measured: `--skills` shipped and USAGE.md still listed four flags, with the
    whole suite green.

    Derived from the real parser on both sides rather than hand-listed, which is the point:
    correcting the one missing flag would leave the next field addition free to repeat it.
    """
    real = _parser_flags(kind, "add")
    assert real, (
        f"walked no flags for `{kind} add` -- for a comparison this is the vacuous-pass "
        f"shape, so the scope is asserted before the contents are")

    usage = dict(_read_all()).get("docs/USAGE.md", "")
    assert usage, "docs/USAGE.md was not readable, so this comparison would pass vacuously"

    documented = _documented_flags(usage, kind, "add")
    assert documented, f"docs/USAGE.md has no `### `job-sluice {kind} add ...`` heading"

    missing = sorted(real - documented)
    assert not missing, (
        f"`job-sluice {kind} add` accepts {missing} but docs/USAGE.md's heading does not "
        f"list them. The flags are generated from EVIDENCE_KINDS[{kind!r}].fields, so a new "
        f"field creates a new flag with no other prompt to document it.")


# ── the triage summary line, derived rather than restated (#223) ──────────────
#
# Every previous version of USAGE.md's `job-sluice triage: ...` sentence went stale
# silently: a field was added to the printed line and the doc kept describing the old
# one, with nothing red. Restating a format string in prose IS the drift surface, so the
# key names are derived from `cli.py` and compared.
# `\{(?:[a-z_]+\()?report\.` -- the optional call wrapper matters. The summary prints
# `failures={len(report.failures)}`, which a bare `=\{report\.` never matched, so
# `failures` fell out of the derived set silently. The scope assertion below pinned the
# five keys that DID match and passed, certifying a doc claim about five of six keys --
# an anti-vacuity check that agreed with the bug it was there to catch.
_TRIAGE_SUMMARY_KEY = re.compile(r"(\w+)=\{(?:[a-z_]+\()?report\.")


def _printed_summary_keys():
    """The `key=` names `cmd_triage_run`'s summary f-string actually interpolates.

    Anchored on `{report.counts}`, not on `print(f"triage: ` -- that shorter prefix also
    matches the #223 re-verdict notice, which is a DIFFERENT `triage: ` line printed
    earlier in the same function and interpolates none of these keys. Caught by the
    vacuity test below, which is the whole reason it is there.
    """
    src = inspect.getsource(cli.cmd_triage_run)
    body = src[src.index('print(f"triage: {report.counts}'):]
    return set(_TRIAGE_SUMMARY_KEY.findall(body[:body.index("file=sys.stderr")]))


def test_the_summary_key_extraction_is_not_vacuous():
    """SCOPE. The verdict below is a subset check, and the empty set is a subset of
    everything -- a regex that stopped matching would certify the doc against nothing.
    Pins the known keys rather than a floor, so one key silently replacing another
    cannot pass."""
    assert _printed_summary_keys() == {
        "judged", "resolved", "llm_calls", "observed_role_types", "backend",
        "failures"}, (
        "the triage summary line changed. Update this set AND the "
        "`job-sluice triage: ...` sentence in docs/USAGE.md that it guards.")


def test_usage_md_documents_every_key_the_triage_summary_prints():
    doc = open("docs/USAGE.md", encoding="utf-8").read()
    line = doc[doc.index("Prints `job-sluice triage:"):]
    line = line[:line.index("Exit 0 always")]
    missing = sorted(k for k in _printed_summary_keys() if f"{k}=" not in line)
    assert not missing, (
        f"docs/USAGE.md's triage summary sentence omits {missing}, so a user reading it "
        "sees a line the tool no longer prints")
