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
import re

import pytest

from sluice.cli import _build_parser
from sluice.core.protocols import EVIDENCE_KINDS

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
    floor that trails reality by 14 asserts nothing. `20` (the ten pre-#164 groups' own
    subcommand counts) is pinned as a literal because it does NOT grow on its own; the evidence
    contribution is DERIVED from EVIDENCE_KINDS (3 subcommands -- add/list/verify -- per kind)
    so a future fourth kind needs no edit here.
    """
    tree = _command_tree()
    assert set(tree) == {
        "ingest", "triage", "cv", "apply", "track", "leads", "health", "mcp", "init", "doctor",
        "experience", "skills", "stories"}, (
        f"the walk found {sorted(tree)} -- a group was added, renamed, or removed; if that is "
        f"intentional, docs/USAGE.md and this set both need updating")
    total_subs = sum(len(v) for v in tree.values() if v is not None)
    expected = 20 + 3 * len(EVIDENCE_KINDS)
    assert total_subs == expected, (
        f"expected {expected} subcommands (20 pre-#164 + 3 per evidence kind), found "
        f"{total_subs} -- the walk is broken, or a group's own subcommand count changed and "
        f"this needs updating")


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
