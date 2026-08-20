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
    empty tree. Pin real structure, not just non-emptiness -- 10 known top-level groups and a
    floor on the total subcommand count across them.
    """
    tree = _command_tree()
    assert set(tree) == {
        "ingest", "triage", "cv", "apply", "track", "leads", "health", "mcp", "init", "doctor"}, (
        f"the walk found {sorted(tree)} -- a group was added, renamed, or removed; if that is "
        f"intentional, docs/USAGE.md and this set both need updating")
    total_subs = sum(len(v) for v in tree.values() if v is not None)
    assert total_subs >= 15, f"only {total_subs} subcommands enumerated -- the walk is broken"


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
     "locations: [London, Manchester]"),
]


def test_no_shipped_doc_instructs_a_retired_config_key():
    checked = 0
    for path, text in _read_all():
        checked += 1
        for pattern, why, _sample in _RETIRED_CONFIG:
            hit = pattern.search(text)
            assert not hit, f"{path} instructs a retired config key -- {why} ({hit.group(0)!r})"
    assert checked >= 5


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
