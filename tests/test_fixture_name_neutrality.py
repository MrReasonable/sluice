"""Guards the FIXTURE-NAME half of the neutrality property.

`test_sluice_neutral_defaults.py` enumerates `sluice/**/config.py` and `sluice.yaml.example`.
Nothing walked `tests/` — so a real employer name typed into a test fixture shipped in a
public repo, and a partial manual rename of it (8 occurrences in one file, 12 left across
four others) read as complete. Caught by CodeRabbit's WEB pass, not by anything local. (#135)

These are RATCHETS, not classifiers. Nothing running locally can tell whether a name belongs
to a real firm — that needs a human or a web lookup. So `_REVIEWED_FIXTURE_IDENTITIES` is a
list somebody has LOOKED AT, and the test fails when a fixture introduces a value that is not
on it. The point is to force that judgement to happen once, when the name is added, instead
of never.

Deliberately NOT a "must match `Example <Word>`" rule. That is not the convention this repo
actually has: `Acme` (17 uses), `A`/`B`/`C`, `Beta`/`Gamma`/`Delta`, `Human Typed Co` and the
deliberately-malformed `Foo\\Bar Ltd` injection fixtures are all legitimate and all fail such
a rule. A guard that fires on ~40 good fixtures gets suppressed, and a suppressed guard
guards nothing.

SCOPE, stated honestly. This sweeps five ENUMERATED positions that carry a lead identity:
frontmatter `company:`, lead-note filenames, `lead_slug=` kwargs, the first positional
argument of the `_note`/`_lead`/`_vault_with`/`_shortlist_with` helpers, and (#164) an evidence-
corpus `Company:` — in frontmatter or in a `fields={"Company": ...}` dict/kwarg, since the
evidence store's fixtures use both and the quoted-key shape hid from the frontmatter-only pattern
entirely. The fourth of those (the identity-first helper) holds a company
in some modules and a slug in others — the same helper name has different signatures per file
— which is why the roster is named for IDENTITIES rather than companies: a leaked employer
name could land in either shape, so both are swept and neither is filtered out. A name written
into some other shape — prose in a comment, a docstring, an unusual helper — is NOT covered;
the email-domain guard below is the broader net.

CV-BODY EMPLOYER LINES are the concrete instance of that gap worth naming, because #167 added
several and none of the four collectors reaches them: a CV fixture is a block of prose, and the
employer sits on a bare line inside it rather than in a frontmatter key, a filename or a helper
argument. `test_cv_fixture_identities_are_on_the_reviewed_roster` below is a NARROW ratchet for
them — it sweeps `Example <Word>` literals in the CV test modules. That shape check is
deliberately weaker than the four positional collectors and must not be mistaken for closing the
gap: it can only ratchet names that ALREADY look synthetic, so a real employer written into CV
body prose still passes everything here. Nothing local can catch that; a human reading the diff
is the only control, which is the same limit this whole file's docstring opens with.

Those collectors all read `tests/**/*.py`. The file has a SECOND half, at the bottom, that
reads `tests/fixtures/*/raw.json` instead — the captured golden payloads, which no collector
here ever walked, which is how real employer names and a real hunt geography shipped publicly
for the repo's whole life (#27) — in `company`, and also in `title` and in URL slugs, which is
the part worth carrying forward: the leak was not confined to the key you would think to check. That half is shaped differently on purpose: two
value rosters for the enumerable keys (`location`, `company`), and a per-source DIGEST for
everything else, because `title` is free text that the boards append the posting's location
to and no roster can enumerate it.

The equal-opportunities collector, added for #133/#107, sweeps a different category entirely: the
equal-opportunities/protected-characteristic fields (`sluice/apply/packet.py`'s `_WARNED_KEYS`
-- ethnicity, religion, disability, gender identity and similar special-category personal
data). SCOPE, stated with the same honesty as the four identity collectors that existed above
it at the time it was added: it matches FOUR fixture shapes a warned-field value can appear
in -- a double- or single-quoted dict-literal
key (`"ethnicity": "..."` / `'ethnicity': '...'`), a double- or single-quoted constructor
kwarg (`ethnicity="..."` / `ethnicity='...'`), and YAML frontmatter, quoted or bare
(`ethnicity: "..."` / `ethnicity: ...`) -- the last of which is the shape the PRODUCTION
reader actually consumes (`core/vault.py`'s `_fm_dict`). A dict-literal key with `**` in front
of it (`CandidateProfile(**{"ethnicity": "..."})`) IS covered -- the dict-literal-key
alternative matches the unpacked form's own text regardless of the `**` -- but a value reached
only through a VARIABLE bound elsewhere (`d = {FIELD: "..."}`) is not, because there is no
literal key or kwarg name at the match site for the pattern to see. Two further shapes are
NOT covered, and measured, not merely argued: ATTRIBUTE ASSIGNMENT on an already-built profile
(`p.ethnicity = "..."`) is excluded by `_EO_BOUNDARY`'s `(?<![\\w.])` lookbehind, which blocks a
preceding `.` specifically to avoid matching ordinary attribute ACCESS -- a deliberate
trade-off, but it also blocks the assignment shape, which is live rather than hypothetical
(`CandidateProfile` is `frozen=False`, so tweaking one field of a built profile this way is the
obvious thing to do in a test); and the TUPLE-PAIR form (`("ethnicity", "...")`) matches no
alternative at all, since it names the field as neither a dict key, a kwarg, nor a frontmatter
line. Nor is a NEW field added to `_WARNED_KEYS` before any fixture uses it (nothing to
sweep yet). That collector is deliberately NOT a roster like `_REVIEWED_FIXTURE_IDENTITIES` --
see its own comment above `_SYNTHETIC_TOKEN` for why a SHAPE ratchet is the right tool there
instead.

The category boundary is narrower than "every demographic-shaped value", too: this collector
is keyed on `_WARNED_KEYS` NAMES (`_EO_FIELDS = tuple(packet._WARNED_KEYS)`), so a
demographic-shaped fixture written under ANY OTHER KEY is invisible to it regardless of shape.
`date_of_birth` is the one example this file used to name -- EO-monitoring-adjacent by
`packet.py`'s own reasoning for why the `age` it derives is warned (see `_WARNED_KEYS`'s
comment below), but the raw DOB is never itself a `_WARNED_KEYS` member (it is excluded from
`_PASSTHROUGH_KEYS` entirely and resolved into `age` instead), so it falls outside `_EO_FIELDS`
and this collector does not see it, even though live fixtures for it already exist
(`tests/test_apply_packet.py`, `tests/test_app_injection.py`). It is not the only one: an
`age_range: <band>` frontmatter fixture (`tests/test_vault_candidate_profile.py`) is an
EO-monitoring age band by shape, sitting in a candidate-note frontmatter position, and is
outside `_EO_FIELDS` for the same structural reason -- `age_range` is not a `CandidateProfile`
field at all, so no key-name check could ever see it.

The candidate-identity collector closes what this docstring previously recorded as an accepted gap
(CodeRabbit, PR #161): the person-data POSITIONS the candidate profile introduces -- the
candidate's own name, contact channels, postal address and the free-text identity fields
beside them. The authoritative list is `_PERSON_DATA_FIELDS` and is deliberately NOT
re-typed here: an enumeration in prose is a second copy that goes stale the moment a field
moves between buckets, which is exactly what happened to the count this paragraph used to
carry. It is a
ROSTER ratchet (`_REVIEWED_CANDIDATE_VALUES`), the same tool as the employer-identity one
above and for the same reason, rather than the SHAPE ratchet the equal-opportunities
collector uses: `SYNTHETIC-ETHNICITY-1` is a fine value for a category nobody has to read,
but a CV headline needs a name that looks like a name, so no shape rule can be imposed and a
human call is the only thing left. It watches the same four fixture shapes as the
equal-opportunities collector, built from the same `_field_value_pattern` helper so the two
cannot drift on the key-boundary or value-quoting subtleties.

What killed the EARLIER attempt is why this one is keyed on FIELD POSITION rather than value
shape. Measured then: a phone-shaped regex analogous to the `_EMAIL` guard below false-
positived on its first run against `tests/test_onboard_emit.py`'s
`"+00 0000\\x0b000000\\x0c0000"` control-character injection fixture (nothing to do with a
phone number), which matched a `+00 0000`-shaped prefix. A position-keyed sweep never looks
at that fixture at all, because it sits under no candidate field name.

That is not to say the position-keyed sweep needed no filter -- it needed one, and measuring
found it rather than reasoning did. Run unfiltered it returned three matches that were Python
SOURCE rather than fixture data (see `_NOT_A_FIXTURE_VALUE`), and it reduced a LinkedIn URL
fixture to the single character `x` by reusing `_identity_of`, whose path-splitting is correct
for a lead identity and wrong for a candidate one (see `_candidate_value_of`). Both are noted
here because a sweep whose filters are invisible is a sweep nobody can judge the scope of.

The evidence `Company:` collector (#164) joins `_IDENTITY_COLLECTORS` itself, not the
dedicated-check group the equal-opportunities and candidate-identity collectors belong to:
an evidence-corpus `Company` names a lead's employer the same way the lead-note
`company:` collector above does, so it belongs on the SAME roster, not a new one. It cannot reuse that existing pattern, though -- the evidence store's field name
is capitalised (`Company`, `EVIDENCE_KINDS["experience"].fields`, not the lead note's lowercase
`company`) and its reader, `core/vault.py`'s `_parse_fm_spaced`, makes quoting OPTIONAL where
the lead-note reader requires it, so a bare `Company: Alpha` is a real fixture this collector
must see and the lowercase-and-quoted pattern above cannot. Both axes are independent: fixing
only the case or only the quoting would still miss the shape this store's own tests actually
write. It covers THREE fixture shapes, not one: frontmatter packed into a Python string literal
(`"---\nCompany: Alpha\n..."`), and the dict/kwarg form `fields={"Company": "Alpha"}` in either
quote style -- the second of which the frontmatter-only version measured `[]` against, because a
quoted key puts a `"` between `Company` and its colon so the literal `Company:` never appears
(#164 review, M6). The bare form is bounded to the next quote, backtick, backslash or real
newline by a LOOKAHEAD rather than by `$` under `re.M`: every frontmatter fixture for this key
packs several lines into one Python string literal joined by an escaped `\n` (two characters,
not an actual newline), so a same-physical-line value never reaches a `$` the way the lead-note
collector's one-key-per-line fixtures do -- measured, an end-of-line-anchored version matched
none of them. The two quoted forms are self-terminating and are deliberately outside that
lookahead, which no real dict literal could satisfy. See the collector's own comment,
immediately above its definition, for the false positives the same measurements found and closed.

A SIXTH position joined this in #168 Task 11's review round: the YAML BLOCK-LIST spelling of
`Company:` (`Company:\n  - Example Alpha`), which `_parse_fm_spaced` (core/vault.py) already
accepts for any frontmatter key. It is swept by `_COMPANY_BLOCK_LIST_COLLECTOR`, a SEPARATE
collector folded directly into `_all_fixture_identities()` rather than added as a sixth member
of `_IDENTITY_COLLECTORS` -- that tuple's own single-capture-group `_collect()` pathway assumes
one identity per match, and a block list can legitimately hold several, so the two need
different post-processing. It reuses `_evidence_block_list_re`, the exact machinery built for
`Skills:`'s own block-list spelling (see that function's docstring); `Company:` is the field
most likely to carry a REAL EMPLOYER NAME, which is the single highest-value thing this whole
file exists to catch, so leaving this shape uncovered on that key was a worse bet than leaving
it uncovered on `Skills:`.
"""
import ast
import dataclasses
import itertools
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from tests.markdown_fences import fenced_blocks, unclosed_fence

import pytest

from sluice.apply import packet
from sluice.core.protocols import CandidateProfile

_TESTS_DIR = Path(__file__).resolve().parent
_SELF = Path(__file__).name

# Reviewed 2026-08-15. Every entry has been eyeballed as synthetic: placeholder words (Acme,
# Foo, Widget, Alpha/Beta/Gamma), single letters and their phonetics (A/B/C/D, Aye/Bee),
# invented compounds (Beavni), descriptive labels (Human Typed Co, Conflicted,
# blank), cluster-test slugs (a1/b2), a job-board name (indeed), the deliberately-malformed
# `Foo\Bar` injection fixtures, and the `Example …` family.
#
# Adding a name here is a DECISION that it names no real firm. Make it deliberately — a local
# check cannot establish it — and prefer `Example <Word>` for anything new.
#
# `indeed` is the one entry allowed on a different ground: it is a shipped source identifier
# (`sluice/ingest/sources/indeed.py`), so it is public integration surface rather than anything
# out of a private job hunt. `.coderabbit.yaml`'s `tests/**` instruction carries that exception
# in full. It is narrow in both directions — the exception covers the identifiers of adapters
# this repo ships, and it does NOT extend to using one as a lead or employer identity, which is
# what this roster governs. `indeed` is here only because a fixture already sits in an identity
# position; a new one should still be `Example <Word>`.
#
# `Unknown` is on the SAME different ground as `indeed`, reviewed 2026-08-17 (#151): it is a
# shipped SENTINEL, one of `core/leads.py`'s `NON_ANSWER_COMPANIES` (the board's own honest
# non-answer, not a name anyone typed hoping it was real). It had already appeared as a bare
# frontmatter value in several other test files without tripping this sweep -- what changed is
# `tests/test_leads_rename.py` (#151's rename pass) being the first to put it in one of the
# FOUR positions this sweep actually watches (a quoted `"Unknown - <role>.md"` filename), so it
# is reviewed here rather than silently matching the roster by accident.
#
# `N-A` is the SAME ground as `Unknown`, reviewed 2026-08-17 (#151, CodeRabbit finding 3): it
# is `_sanitize`'s length-preserving rendering of the sentinel "N/A" (`/` maps to `-` because
# `/` is a filename-illegal path separator), not a name anyone typed hoping it was real --
# `NON_ANSWER_COMPANIES` in core/leads.py lists "n/a" itself as one of its members.
# `tests/test_leads_rename.py`'s sanitize-aware placeholder-head test is what put it in the
# filename position this sweep watches.
# The CV-BODY identities (#167). Added when the narrow CV ratchet at the bottom of this file
# first swept them -- before it existed, NO collector here saw a name written into CV fixture
# prose, so these eleven were live identities that never forced the roster call this file
# exists to force. Each follows the repo's established `Example <Word>` synthetic convention,
# and several are not employers at all (`Example Candidate`, `Example Location`,
# `Example Cert`, `Example University`, `Example Decoy` name a person, a place, a
# certificate, a school and a deliberate fabrication-gate decoy).
# `Example Alpha` (task 8, #174): reached only once the CV module set below stopped being a
# hand-list and started covering `test_onboard_questions.py` -- a placeholder employer
# probing the employers gate's case-sensitivity, same `Example <Word>` construction as the
# rest of this roster. Owner's ruling, 2026-08-24: invented.
# `EXAMPLE CO`/`example co` (#205): NOT new identities. They are casings of `Example Co`,
# already on this roster, and #205's subject is precisely that a board renders one employer
# several ways -- so a fixture for it cannot be written without both spellings. They are
# listed rather than folded into the comparison because the ruling this roster records is
# "does this name a real firm", and that ruling was already made for `Example Co`; case
# cannot change its answer. Folding the SWEEP instead (so any casing of a reviewed value
# passes) would be the same one-identity-many-spellings fix this issue applies to the vault,
# one rung out, and is deliberately left as its own decision rather than ridden in on this
# branch -- widening a neutrality gate is not a side effect worth taking silently.
_REVIEWED_FIXTURE_IDENTITIES = frozenset({
    "A", "A-B", "Acme", "Alpha", "Aye", "B", "Beavni", "Bee", "Beta", "C", "Conflicted",
    "D", "Delta", "E", "Epsilon", "Example", "Example Alpha", "Example Analytics",
    "Example Beta",
    "Example Candidate", "Example Cartography", "Example Cert", "Example Cloud",
    "Example Co", "EXAMPLE CO", "example co",
    "Example Data", "Example Decoy", "Example Leverage",
    "Example Location", "Example Practitioner", "Example Publication",
    "Example Robotics", "Example Scrum",
    "Example Synergy", "Example University",
    "Example Foundry", "Example Ltd", "Example Meridian", "Example MeridianRemote",
    "Example Northgate",
    "Example Systems", "Example Telemetry", "Example Tidal", "Foo", "Gamma",
    "Human Typed Co", "N-A", "Unknown", "Widget", "X",
    "a", "a1", "a2", "b", "b1", "b2", "blank", "c", "d", "example-lead",
    # Structural slugs, not names: they mean "this lead" and "a different lead", and the
    # #203 same-slot rule needs two distinguishable ones to express its lead scoping.
    # `other-lead` reached this roster because the fixture kwarg was renamed to
    # `lead_slug=`, the position this file's collector actually sweeps -- it had been
    # sitting in an unswept `lead=` kwarg.
    "other-lead",
    # Escaping/injection fixtures — the backslashes are the point of the test.
    "Foo\\Bar Ltd", "Foo\\\\Bar Ltd", "Foo\\\\g<0>Bar", "Foo\\\\nBar",
})


def _test_sources():
    """Every test module's text, EXCEPT this one.

    Self-exclusion is load-bearing: this file contains the collector regexes and the roster as
    string literals, so scanning it makes the sweep match its own source and report nonsense
    like `'([^'` as an unreviewed employer name.
    """
    return [p.read_text(encoding="utf-8")
            for p in sorted(_TESTS_DIR.rglob("*.py")) if p.name != _SELF]


def _identity_of(raw):
    """The lead identity in a fixture value that may be a path, a `.md` name, or a full
    `Company - Role` slug. Order matters: strip the extension, then the directory, then the
    role — a path segment can itself contain ` - `.

    The leading quote-strip exists for the equal-opportunities collector below, not for the
    four identity collectors: `_EO_PATTERN`'s single capture group has to cover a QUOTED value
    (double- or single-quoted) as well as a bare YAML-frontmatter one in the same group, so the
    quoted alternatives capture the wrapping quote characters along with the text — `findall`
    only returns one string per match, so there is nowhere else to separate "was this quoted"
    from the text itself. Stripping a matching leading/trailing quote here is a no-op for the
    other four collectors: each of their own capture groups already excludes the quote
    character from what it matches (e.g. `"([^"]*)"`), so `raw[0] == raw[-1] == '"'` is never
    true for their output to begin with. The path/role-splitting logic below it (`.md`
    suffixes, `" - "` slugs) is similarly meaningless for an equal-opportunities VALUE — a
    token like `SYNTHETIC-ETHNICITY-1` has no `.md` suffix or `" - "` in it to strip — but
    harmless for the same reason: a demographic value containing that exact substring by
    coincidence is not a realistic fixture shape, so the shared helper is reused as-is rather
    than forked for one caller."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return raw.removesuffix(".md").split("/")[-1].split(" - ")[0].strip()


def _evidence_field_re(key: str):
    """The collector pattern for ONE capitalised evidence frontmatter key.

    Parameterised rather than copied because every hardening below was MEASURED against the
    real corpus for `Company:` and applies unchanged to any sibling key -- most of all the
    escaped-`\\n` shape, where a whole frontmatter block is one Python string literal joined
    by two literal characters (backslash, `n`) rather than real newlines. A second key given
    its own hand-written regex would start from the naive `key:\\s*"(...)"` that was tried
    first here and measured to match NOTHING real, and would then drift from this one every
    time either was corrected.

    See the long comment above for what each alternative and the lookahead terminator close.

    The gap AFTER `:` is `[ \\t]*`, not `\\s*` -- `\\s` matches a REAL newline too, and a
    bare `key:` immediately followed by one (a triple-quoted fixture, rather than the
    packed-escape shape every real fixture here otherwise uses) let the value alternative's
    `\\s`-excluding first-char class start matching on the FOLLOWING physical line instead
    of refusing to match at all. Measured via #168 Task 11's own block-list planting
    witness: a `Skills:\\n  - Example Torrent\\n` fixture (a bare `key:` with no inline
    value, immediately followed by a YAML block-list item) collected `- Example Torrent`,
    dash included, as a bogus SECOND identity alongside the correct one the block-list
    collector found. `[ \\t]*` still allows the ordinary `Company: Alpha` gap (a same-line
    space) and does not change a single one of the four shapes
    `test_the_evidence_company_collector_sees_every_shape_it_claims_to` pins -- only the
    cross-newline case, which no real fixture here has ever relied on.
    """
    return re.compile(rf"""["']?{key}["']?\s*:[ \t]*("[^"{{\n]+"|'[^'{{\n]+'"""
                      rf"""|[^\s"'`{{\\\n][^"'`{{\\\n]*?(?=["'`\\\n]|\s*$))""", re.M)


def _evidence_block_list_re(key: str):
    """The YAML block-list spelling of `{key}:` -- `_parse_fm_spaced` (core/vault.py)
    reads `key:\n  - a\n  - b` and joins it into the identical comma string
    `_evidence_field_re` catches, and that shape is already LIVE for `Category:`
    (`tests/test_core_vault_cv.py`'s `test_read_experience_parses_block_list_category`).
    `_evidence_field_re`'s own alternation cannot see it at all -- measured: every
    alternative there requires a value TOKEN immediately after `key:`, and a block list
    has none; the value starts on the next line instead. A `Skills:` fixture written the
    way `Category:` already is would sweep clean over `_SKILL_COLLECTOR` alone.

    PROMOTED here from a Skills-only helper (#168 Task 11 review): the SAME machinery,
    unchanged, now also feeds `_COMPANY_BLOCK_LIST_COLLECTOR` below. `Company:` is the
    field most likely to carry a REAL EMPLOYER NAME, the single highest-value thing this
    whole file exists to catch, so leaving this shape uncovered there was a worse bet
    than leaving it uncovered on `Skills:` -- reusing the exact `key`-parameterised
    pattern this function was already built with, rather than forking a second near-copy.

    The line-break between `key:` and the first `- item`, and between each `- item` in
    the run, is a REAL newline or the literal two-character `\n` escape ALIKE -- every
    packed-frontmatter fixture in this repo joins its lines with the escape (one Python
    string literal), never a real newline, so treating only the real character as a
    boundary would catch nothing that actually exists. Captures the WHOLE run of `- item`
    lines as one string; the caller splits it into per-item values, mirroring the comma
    spelling's own post-findall split.
    """
    nl = r"(?:\\n|\n)"
    item = r'''"[^"{\n\\]+"|'[^'{\n\\]+'|[^\s"'`{\\\n][^"'`{\\\n]*?(?=["'`\\\n]|\s*$)'''
    return re.compile(rf'''["']?{key}["']?\s*:[ \t]*{nl}((?:[ \t]*-[ \t]*(?:{item}){nl})+)''')


def _block_list_items(pattern, text) -> list:
    """Every `- item` line inside ONE captured block-list run of `text`.

    Normalises the literal `\n` escape to a real newline FIRST, so one
    `str.splitlines()` call treats both spellings of a line break identically -- the same
    rule `_evidence_block_list_re` anchors on, kept in exactly one place so the two
    cannot drift apart. Shared by both the Skills roster sweep and the Company identity
    sweep (`_collect_block_list`, below `_collect`) -- and by both shape-coverage tests
    -- for the same reason: one extractor for a shape two independent fields now use.
    """
    items = []
    for run in pattern.findall(text):
        for line in run.replace("\\n", "\n").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if item:
                    items.append(item)
    return items


_IDENTITY_COLLECTORS = (
    ("frontmatter company:", re.compile(r'company:\s*"([^"]*)"')),
    ("lead-note filename", re.compile(r'"([A-Za-z][^"\n]*? - [^"\n]*?\.md)"')),
    ("lead_slug= kwarg", re.compile(r'lead_slug="([^"\n]*)"')),
    # `(?<![A-Za-z0-9])` so the helper NAME must start here. Without it this matched the tail
    # of `_row_to_lead("indeed", ...)` -- whose first argument is a SOURCE ID, not a lead
    # identity -- and the roster carried `indeed` purely to silence that false positive. A
    # sweep that has to be appeased with entries for things it misread is a sweep nobody can
    # read the roster of.
    ("identity-first helper",
     re.compile(r'(?<![A-Za-z0-9])_(?:note|lead|vault_with|shortlist_with)\("([^"\n]*)"')),
    # Evidence-corpus frontmatter (#164) is a DIFFERENT shape from the lead frontmatter
    # `frontmatter company:` above on two independent axes, so that collector cannot see it:
    # the key is capitalised (`Company:`, matching `EVIDENCE_KINDS["experience"].fields`,
    # not the lowercase lead-note `company:` key), and `_parse_fm_spaced` -- the reader this
    # shape actually feeds (core/vault.py) -- makes quoting OPTIONAL, so a bare
    # `Company: Alpha` is as real a fixture as a quoted `Company: "Alpha"`.
    #
    # This is NOT `company:\s*"([^"]*)"` with the case flipped and the quotes made optional
    # -- that shape was tried first and measured to match NOTHING real. Every actual fixture
    # (`tests/test_evidence_store.py`) packs several frontmatter keys into ONE Python string
    # literal joined by an ESCAPED `\n` -- two literal characters, backslash then `n`, not a
    # real newline -- so `Company: Alpha\nverified: ...` is entirely one physical SOURCE
    # line. A pattern anchored on `$` (even under re.M) then has to reach that physical
    # line's true end, which is past the closing quote and often a trailing `)` or `,` --
    # never immediately after "Alpha". So the terminator here is a LOOKAHEAD for the next
    # boundary character -- a real quote, a backtick, a backslash (the start of that same
    # `\n` escape, or of `\\`), or an actual newline -- rather than a match-and-consume `$`.
    # Measured against the real corpus: this reads `Alpha`/`Beta`/`Gamma`
    # (tests/test_evidence_store.py) and `Example Foundry`/`Example Systems`/`X`
    # (tests/test_core_vault_cv.py, both bare and double-quoted) cleanly, with no fixture
    # rewritten to make it so.
    #
    # A THIRD shape carries the same fact and was missed entirely (#164 review, M6): the
    # kwarg/dict-literal `fields={"Company": "Alpha", ...}`, which is what
    # `tests/test_mcpserver.py` itself writes for its evidence fixtures. Measured, the
    # frontmatter-only pattern matched `[]` against it -- the key is QUOTED there, so
    # `Company` and `:` are separated by a `"` and a literal `Company:` never appears.
    # The key's quotes are therefore optional on both sides, and the VALUE now covers all
    # three spellings in ONE capture group (double-quoted, single-quoted, bare), the same
    # way `_EO_VALUE` below does and for the identical reason: `findall` returns one
    # string per match, so `_identity_of`'s wrapping-quote strip is where "was this
    # quoted" gets separated from the text. Single quotes are live style here -- ruff's
    # configured rule set has no `Q` rules -- so `'Company': 'Beta Ltd'` is a real shape,
    # not a hypothetical one.
    #
    # The LOOKAHEAD terminator sits inside the BARE alternative only. The two quoted
    # alternatives are self-terminating, and applying the lookahead to them would refuse
    # every real dict literal: after `"Alpha"` the next character is a `,` or a `}`,
    # neither of which is a boundary the bare form's lookahead accepts.
    #
    # Excluding `{` from EVERY alternative (and hence from what the lookahead can stop
    # short of matching) closes a second measured hole: an f-string interpolation whose
    # own value is itself quoted, `f'Company: "{e["company"]}"'` (tests/harness/config.py),
    # nests a SECOND `"` inside the outer one, which stops a naive capture at `{e[` --
    # short of the inner closing `"`, so the whole `{...}` never appears in the captured
    # text and `_is_source_text`'s balanced-brace check (which needs both delimiters) has
    # nothing to recognise as an interpolation. Excluding `{` altogether means the capture
    # cannot start past that character at all, so this line now matches nothing instead of
    # leaking `{e[` as a bogus identity. The quoted alternatives exclude it too, and had
    # to be measured for it separately: `"[^"\n]+"` happily matched the `"{e["` on that
    # same line and put `{e[` straight back into the roster.
    #
    # The captured value's FIRST character must be non-whitespace for a similar reason: a
    # GREEDY `\s*` before the optional quote backtracks into the capture group when the
    # straightforward parse fails, and on that same harness/config.py line it backtracked
    # into matching a single space (`"?` giving back the real quote it had consumed, `\s*`
    # giving back the one space it had consumed, leaving the space free for the capture
    # group to claim, followed immediately by a real quote the lookahead is happy to
    # stop at) -- a bogus one-character identity `" "`, measured, not hypothetical. A plain
    # `\s*` glued onto a class that ALLOWS its first char to be whitespace cannot tell
    # "there is no real value here" apart from "the value legitimately starts after some
    # whitespace"; splitting the class so the first captured character must be non-blank
    # closes that without a possessive quantifier (no other regex in this file uses one).
    # None of the real fixtures need a leading space inside the value, and `Example Foundry`
    # / `Example Systems` still keep their INTERNAL space via the wider class that follows.
    ("evidence Company: (frontmatter or dict/kwarg)", _evidence_field_re("Company")),
)

# The YAML block-list spelling of `Company:` (#168 Task 11 review) -- a SEPARATE
# collector, deliberately NOT folded into `_IDENTITY_COLLECTORS` above. That tuple's
# `_collect()` consumer applies `_identity_of` to ONE captured value per match; a block
# list can legitimately hold SEVERAL items in one match (`_evidence_block_list_re`
# captures the whole run), so it needs `_block_list_items` to split it first --
# different post-processing, not a different regex. Folded into `_all_fixture_identities`
# directly, below, via `_collect_block_list`. Also kept out of `_COLLECTORS` (further
# down): unlike the five in `_IDENTITY_COLLECTORS`, no real `Company:` block-list fixture
# exists in this repo yet, so `test_every_collector_actually_finds_fixtures`'s `>= 2`
# anti-vacuity floor would fail on it immediately -- the same reason `_SKILL_COLLECTOR`
# and `_SKILL_BLOCK_LIST_COLLECTOR` sit outside both tuples too.
_COMPANY_BLOCK_LIST_COLLECTOR = ("evidence Company: (YAML block list)",
                                 _evidence_block_list_re("Company"))


# Derived from the packet's own warned-field classification (sluice/apply/packet.py's
# `_WARNED_KEYS`), not hand-listed. `_WARNED_KEYS` is itself the derived remainder of
# `_PASSTHROUGH_KEYS - _DETAIL_KEYS` -- which fields count as protected-characteristic /
# equal-opportunities data is a legal judgement `packet.py` already makes once; re-typing the
# field names here would give this file its own copy to drift out of sync with a future
# reclassification. `marital_status`, `nationality` and `dual_nationality` joined
# `_WARNED_KEYS` after this feature's own review round; `age` moved into the same rendered
# warned SECTION of render_text's output at the same time but is NOT, and cannot be, a member
# of THIS tuple -- it is derived from `date_of_birth`, not a `CandidateProfile` field, and
# packet.py's own `_WARNED_KEYS` comment says so explicitly ("it is not, and cannot be, a
# member of THIS tuple"). Importing the tuple directly means this collector inherits any
# future reclassification for free.
_EO_FIELDS = tuple(packet._WARNED_KEYS)
_EO_KEY = "(?:" + "|".join(_EO_FIELDS) + ")"
# Four fixture shapes carry a warned-field VALUE in this repo's tests, or could plausibly be
# used to add one, and a pattern that matches only some silently misses the rest:
#   double-quoted dict-literal  "ethnicity": "SYNTHETIC-ETHNICITY-1"  (tests/test_apply_packet.py)
#   double-quoted kwarg         ethnicity="SYNTHETIC-ETHNICITY-1"     (tests/test_candidate.py,
#                               via CandidateProfile(ethnicity=...))
#   single-quoted dict/kwarg    'ethnicity': '...' / ethnicity='...'  -- ruff's configured rule
#                               set (E4,E7,E9,F -- no `Q` rules, no `ruff format --check` in CI)
#                               permits single quotes, so this is live style, not hypothetical.
#   YAML frontmatter            ethnicity: "..." / ethnicity: ...    -- the shape the PRODUCTION
#                               reader actually consumes (core/vault.py's `_fm_dict`: a bare
#                               `key: value` line, value stripped of a surrounding quote either
#                               way or left bare) -- see tests/test_app_injection.py's
#                               `profile_fm = 'town: "Example Town"\ndate_of_birth: ...\n'` for
#                               a fixture already using this exact shape for a different field.
# `_EO_VALUE` covers all three value spellings (double-quoted, single-quoted, bare) in ONE
# capture group, because `_collect`/`findall` only carries one string per match; `_identity_of`
# above is what strips a matching wrapping quote back off afterward.
_EO_VALUE = r'(?:"[^"]+"|\'[^\']+\'|[^"\'\n\\]+)'
# The dict/frontmatter-quoted-key form quotes the key (either quote style) and separates with
# `:`; the kwarg and bare-YAML forms have an unquoted Python-identifier-shaped key and separate
# with `=` or `:`. `_EO_BOUNDARY` guards the unquoted-key alternative the same way the
# identity-first helper collector above guards its own helper name -- without SOME guard, a
# hypothetical `sub_ethnicity=` would match on the `ethnicity=` tail. A plain `(?<![\w.])`
# negative lookbehind is not quite right here, though: measured against a real fixture shape
# (`'town: "..."\nethnicity: "..."\n'`), the character immediately before `ethnicity` is the
# `n` that ends the PRECEDING key's `\n` line-break escape as it appears in Python SOURCE TEXT
# -- a word character, indistinguishable at one character of lookback from the `_` in
# `sub_ethnicity`. `_EO_BOUNDARY` allows the match in that specific case (preceded by a
# literal 2-character backslash-escape) while still blocking a genuine identifier-suffix
# collision; verified against both shapes directly (not merely reasoned about) via
# `test_the_equal_opportunities_pattern_recognizes_all_four_fixture_shapes` below, which
# exercises both the escape-tail case and the `sub_ethnicity=` guard explicitly.
_EO_BOUNDARY = r'(?:(?<![\w.])|(?<=\\[a-z]))'


def _field_value_pattern(fields, *, capture_field=False):
    """The four fixture shapes above, for an arbitrary set of `CandidateProfile` field
    names. Extracted from `_EO_PATTERN`'s original inline construction so the
    person-data collector below is built from the SAME key-boundary and value
    alternation rather than a second hand-written copy of them: those two pieces are
    where every measured subtlety lives (`_EO_BOUNDARY`'s escape-tail case, `_EO_VALUE`
    covering three quote spellings in one group), and a second copy is a second thing to
    get wrong silently.

    `capture_field=True` adds a capture group around the key. It is a separate mode
    rather than the default because `_collect`/`findall` carries exactly one string per
    match, so the collectors registered in `_COLLECTORS` must stay single-group; the
    field-aware callers use `finditer` and read the groups by position.
    """
    key = "(?:" + "|".join(fields) + ")"
    quoted_key = f"({key})" if capture_field else key
    bare_key = f"({key})" if capture_field else key
    return re.compile(
        r'(?:["\']' + quoted_key + r'["\']\s*:|' + _EO_BOUNDARY + bare_key + r'\s*[:=])'
        r'\s*(' + _EO_VALUE + r')')


_EO_PATTERN = _field_value_pattern(_EO_FIELDS)
# The field-aware twin, used only by the token test below. Built from the same helper on
# the same `_EO_FIELDS`, so the two cannot disagree about WHICH fixtures exist -- only
# about how many groups they hand back. `test_the_two_equal_opportunities_patterns_find_
# the_same_values` pins that executably rather than leaving it to this comment.
_EO_PAIR_PATTERN = _field_value_pattern(_EO_FIELDS, capture_field=True)

# ── the candidate's own identity fields ──────────────────────────────────────
#
# `_EO_FIELDS` above covers the twelve SPECIAL-CATEGORY fields. It leaves the ordinary
# identity ones -- the candidate's name, contact channels and postal address -- swept by
# nothing, which is the gap this file's module docstring used to record as unbuilt and
# CodeRabbit re-raised on PR #161. They are the plainest personal data this repo handles:
# a real surname or postcode in a fixture is exactly what the neutrality rule exists to
# keep out of a public repo.
#
# The three-way split below is EXHAUSTIVE over `CandidateProfile` and asserted to be by
# `test_every_candidate_profile_field_is_classified`, so a field added to the dataclass
# reddens the build until someone decides which bucket it belongs in. That inversion is
# deliberate and matches `packet.py`'s own `_WARNED_KEYS`: forgetting to classify must
# over-protect, never under-protect.
_PERSON_DATA_FIELDS = (
    "forenames", "surname", "email", "mobile", "linkedin",
    "address_line1", "address_line2", "town", "county", "postcode", "country",
    # `date_of_birth` and `first_language` are here rather than below because neither is
    # a yes/no, a closed set, or a setting -- which is what that bucket's rationale says
    # it holds, and a bucket whose stated reason does not cover its own contents is the
    # "a comment that states a mechanism nothing falsifies" shape (CodeRabbit, PR #161).
    # A real DOB is squarely identifying. NB `nationality`/`dual_nationality` are NOT
    # here and do not belong here: both are already `_WARNED_KEYS` members, so the
    # equal-opportunities collector sweeps them under the stricter token rule.
    "date_of_birth", "first_language",
)
# Neither special-category nor free-text identity: a yes/no answer, an honorific drawn
# from a tiny closed set, or a `how_heard` SETTING that is sluice's own behaviour rather
# than anything about the person. A realistic-looking value in one of these positions
# cannot identify anybody, so a reviewed roster would be pure friction. Every member is
# one of those three; if a field added here is not, it belongs above.
_NON_IDENTIFYING_FIELDS = (
    "requires_uk_work_permit", "right_to_work_uk", "currently_employed_by_them",
    "previously_employed_by_them", "referred_by_current_employee",
    "how_heard_default", "how_heard_detail_from_lead_source", "honorific",
    "served_armed_forces", "caring_responsibility", "worked_in_construction",
)

def _packaging_author_emails():
    """The project's own declared author address(es), read from `pyproject.toml`.

    `tests/test_packaging.py` asserts the wheel ships this identity, so the address
    legitimately appears in an `email` position in `tests/` and the sweep legitimately
    finds it. It is the one non-synthetic value the candidate roster admits -- and the
    only honest way to admit it is to DERIVE it from the package metadata that already
    publishes it, rather than typing a copy into a guard file whose subject is keeping
    exactly such values out of fixtures. A copy here would be a third one (after
    `pyproject.toml` and `test_packaging.py`'s own constant), free to drift, and a fresh
    publication in its own right.

    Scoped to the `email` key alone in the roster below: this is package metadata, not a
    candidate identity, and it must not be accepted in a `forenames` or `surname`
    position on the strength of appearing here.
    """
    import tomllib

    with open(_TESTS_DIR.parent / "pyproject.toml", "rb") as fh:
        authors = tomllib.load(fh).get("project", {}).get("authors", [])
    return frozenset(a["email"] for a in authors if a.get("email"))


# Reviewed 2026-08-20. Every value below was collected by running the sweep, then
# eyeballed as synthetic: standard programming placeholders (`Ada`, and `Jane`/`Roe` from
# the `Jane Roe` legal-placeholder convention), descriptive labels naming what the fixture
# is FOR (`Distinctive`, `Candidate`, `Real`, `RunOneSurname`, `NotAField`, `Cy`), the
# `Example …` family, RFC 2606 `example.invalid` mail and web addresses, and the two
# reserved phone ranges already in consistent use (`+1 555 0100`, `+44 20 7946 0000`).
#
# Adding a value here is a DECISION that it identifies nobody. Make it deliberately --
# nothing local can establish it -- and prefer the `Example …` / `example.invalid` /
# reserved-range conventions for anything new. This is the same ratchet as
# `_REVIEWED_FIXTURE_IDENTITIES` above, applied to the candidate rather than the employer;
# it is a forcing function for a human call, not a classifier.
#
# The two `test_packaging.py` entries are on a DIFFERENT ground, the same way `indeed` is
# on the identity roster: they are the repo's own published commit-trailer address, read
# out of `CLAUDE.md`'s commit convention and asserted against by the packaging test. That
# is deliberate public project metadata, not a fixture standing in for a candidate -- and
# it is on the roster rather than excluded by filename, because a path exclusion would
# hide every FUTURE leak in that file too.
#
# Keyed BY FIELD, not one flat set (CodeRabbit, PR #161). `_collect_person_data` goes to
# some trouble to keep the position alongside the value, and subtracting a flat roster
# threw it away again -- which had two effects worth naming, because both read as
# "reviewed" while being nothing of the sort. The commit-trailer address became an
# accepted value in EVERY candidate position, so a future `email=` on a candidate could
# be that address and pass, even though it was reviewed only as packaging metadata. And
# a reviewed forename was accepted as a postcode, a reviewed phone number as a surname.
# Per-field, a value is reviewed only where it was actually looked at.
_REVIEWED_CANDIDATE_VALUES = {
    "forenames": frozenset({
        "Ada", "Ada  Grace", "Candidate", "Cy", "Distinctive", "Example", "Jane",
        "NotAField", "Real",
    }),
    "surname": frozenset({"Candidate", "Example", "NotAField", "Roe", "RunOneSurname"}),
    # The project's own declared author address is DERIVED, never typed here -- see
    # `_packaging_author_emails`. It is the one value in this roster that is not synthetic,
    # and a literal copy of it would be both a third copy to drift and a fresh publication
    # of it in a file whose whole subject is keeping such values out.
    "email": frozenset({
        "ada@example.invalid", "distinctive@example.invalid",
        "example.person@example.invalid",
    }) | _packaging_author_emails(),
    "mobile": frozenset({
        "+1 555 0100", "+44 20 7946 0000", "Phone: +1 555 0100",
        # the deliberate DOUBLE space is the fixture: tests/test_cv_engine.py's
        # rewording fixtures need a declared contact whose internal spacing a composer
        # can collapse, since `contact_block` (unlike `full_name`) preserves runs.
        "Phone: +1 555  0100",
    }),
    "linkedin": frozenset({
        "https://example.invalid/in/example/", "https://example.invalid/in/x",
    }),
    "town": frozenset({"Example Town"}),
    # Two shapes, both invented: an ISO date and a deliberately NON-ISO one that exists
    # to drive `age_from_dob`'s unparseable-input arm. Reviewed with the maintainer.
    "date_of_birth": frozenset({"1990-06-15", "15/06/1990"}),
}

_PERSON_PATTERN = _field_value_pattern(_PERSON_DATA_FIELDS)
_PERSON_PAIR_PATTERN = _field_value_pattern(_PERSON_DATA_FIELDS, capture_field=True)

# Measured, not assumed: run unfiltered, this sweep returns three matches that are not
# fixture values at all, and all three are PYTHON SOURCE rather than data --
# `surname = cv_name.partition(` and a `linkedin` key whose bare value ran on into the
# next YAML key (`searches:`), both in `tests/harness/config.py`. Every genuine
# person-data fixture measured across `tests/` is a quoted string literal; every
# false positive is an unquoted fragment of an expression. So the filter is on the VALUE
# SHAPE -- a bracket or a trailing colon cannot occur inside a real name, address or
# contact channel -- rather than on the FILE, because excluding `tests/harness/config.py`
# by name would hide every future leak in it as well. Stated here because a filter that
# silently drops matches is how a sweep comes to find nothing at all.
_NOT_A_FIXTURE_VALUE = re.compile(r'[()\[\]]|:\s*$')
# An UNEXPANDED interpolation is source text, not fixture data. `_collect` has always
# skipped a value STARTING with `{`, which catches a bare `"{forenames}"` template but not
# a placeholder embedded mid-string -- and the one live example of the latter,
# `f"Author-email: MrReasonable <{NOREPLY_AUTHOR_EMAIL}>"` in tests/test_packaging.py,
# was consequently collected as though it were a candidate email fixture and had to be
# rostered to silence it. Rostering source text is how a roster stops meaning anything.
# Measured across tests/: exactly four candidate-position values contain a brace, and all
# four are unexpanded interpolations (three `{forenames}`/`{surname}`/
# `{DEFAULT_CANDIDATE_MOBILE}` templates in tests/harness/config.py, plus the f-string
# above), so this drops no legitimate fixture.
_INTERPOLATION = re.compile(r"\{[^}]*\}")


def _is_source_text(value) -> bool:
    """Is this collected string PYTHON SOURCE rather than fixture data?

    One predicate for every collector in this file, because they all want the same
    answer and previously spelled it three different ways -- `value.startswith("{")` in
    three places and an `_INTERPOLATION` search in a fourth. That divergence is what let
    a mid-string placeholder through the candidate collector while the bare-template case
    was handled everywhere (CodeRabbit, PR #161).

    Measured across `tests/` when this was unified: only the candidate collector had a
    LIVE instance (`f"Author-email: MrReasonable <{...}>"`, an f-string's own source).
    Every OTHER collector collects no mid-string interpolation today, so applying the
    predicate to them changes nothing now and closes the same latent hole -- which is the
    point, since the alternative is those sites drifting apart again.

    No count here, deliberately: this docstring said "the other five" while there were
    six, one collector after the list grew (#164 review, L1). A number in prose beside a
    growing tuple is a drift surface with nothing to hold it -- and `_COLLECTORS` is
    right there for anyone who wants the total, pinned by
    `test_the_collector_split_this_file_documents_is_the_split_it_has`.
    """
    return not value or bool(_INTERPOLATION.search(value))


def _candidate_value_of(raw):
    """Strip a wrapping quote and nothing else.

    Deliberately NOT `_identity_of`, whose path- and role-splitting is right for a LEAD
    identity and actively wrong here: measured, it reduced the fixture
    `linkedin="https://example.invalid/in/x"` to `'x'` by treating the URL as a
    directory path and keeping only the last segment. A roster entry of `x` would then
    review nothing at all, and the same stripping would cut a real address line at its
    first ` - `. A candidate identity value is the whole value.
    """
    raw = raw.strip()
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return raw.strip()


def _collect_person_data():
    """`{field: {value, ...}}` for every candidate identity position in `tests/`.

    Field-keyed rather than a flat value set, so a reviewer reading a failure sees WHICH
    field carries the unreviewed value -- a bare `'Roe'` in a list says much less than
    `surname='Roe'`.
    """
    found = {}
    for text in _test_sources():
        for m in _PERSON_PAIR_PATTERN.finditer(text):
            field, raw = (m.group(1) or m.group(2)), m.group(3)
            value = _candidate_value_of(raw)
            if _is_source_text(value) or _NOT_A_FIXTURE_VALUE.search(value):
                continue
            found.setdefault(field, set()).add(value)
    return found


_COLLECTORS = _IDENTITY_COLLECTORS + (
    ("equal-opportunities values", _EO_PATTERN),
    ("candidate identity values", _PERSON_PATTERN),
)
# `test_every_collector_actually_finds_fixtures` below parametrizes over the FULL
# `_COLLECTORS` tuple, so the equal-opportunities collector gets that anti-vacuity check
# for free -- exactly the same reasoning that motivates it for the five identity
# collectors. The two roster-completeness tests further down (`_all_fixture_identities`
# and its two callers) deliberately read `_IDENTITY_COLLECTORS`, NOT `_COLLECTORS`: they
# police `_REVIEWED_FIXTURE_IDENTITIES`, a roster documented above as being about LEAD
# identities (company names, board slugs) -- folding SYNTHETIC-<FIELD>-<N> tokens into
# that same roster would require reviewing them as if they were candidate employer names,
# which they are not, and would blur a roster this file already goes to some length to
# scope honestly. The equal-opportunities collector gets its OWN dedicated shape check
# instead: test_every_equal_opportunities_fixture_value_is_an_obvious_synthetic_token,
# below.


def _collect(pattern):
    found = set()
    for text in _test_sources():
        for raw in pattern.findall(text):
            name = _identity_of(raw)
            # Blank/whitespace fixtures and interpolation placeholders carry no identity.
            if _is_source_text(name):
                continue
            found.add(name)
    return found


def _collect_block_list(pattern):
    """The block-list sibling of `_collect`, for one `_evidence_block_list_re` pattern.

    Same normalisation as `_collect` -- `_identity_of` then `_is_source_text` filtering
    -- applied to EACH item `_block_list_items` splits out of a captured run, so a
    block-list identity is held to the identical bar as the five `_IDENTITY_COLLECTORS`
    members. Kept separate from `_collect` itself rather than folded in behind an
    `if`: `_collect` takes a SIMPLE-VALUE pattern (`pattern.findall` already yields the
    identity), while this one's pattern yields a multi-item RUN that needs splitting
    first -- different shapes at the call site, not a cosmetic variant.
    """
    found = set()
    for text in _test_sources():
        for raw in _block_list_items(pattern, text):
            name = _identity_of(raw)
            if _is_source_text(name):
                continue
            found.add(name)
    return found


def _all_fixture_identities():
    names = set()
    for _label, pattern in _IDENTITY_COLLECTORS:
        names |= _collect(pattern)
    names |= _collect_block_list(_COMPANY_BLOCK_LIST_COLLECTOR[1])
    return names


def test_the_evidence_company_collector_sees_every_shape_it_claims_to():
    """#164 review, M6. The collector was frontmatter-only, so it matched `[]` against
    `fields={"Company": "Alpha"}` -- the shape `tests/test_mcpserver.py` itself writes
    for its evidence fixtures, and the shape any test driving `Sluice.add_evidence` or
    the store's `propose_evidence` naturally reaches for. A quoted key puts a `"`
    between `Company` and its colon, so the literal `Company:` never appears at all.

    Every shape the collector's own comment claims is exercised here, on synthetic
    strings rather than on whatever the repo happens to contain today: the ones it must
    SEE, and the ones it must NOT report. `test_every_collector_actually_finds_fixtures`
    covers the "finds something real at all" half; this covers WHICH shapes, where a
    silent regression to any one of them just looks like a quiet corpus.

    The strings below are written as the collector sees them -- fragments of Python
    SOURCE. `\\n` inside them is two characters, backslash then `n`, exactly as it is in
    the real fixtures (`tests/test_core_vault_cv.py`), because a frontmatter fixture is
    a Python string literal packing several lines onto one physical source line. This
    file is `_SELF`, the one file `_test_sources()` excludes, so none of these values
    reaches `_REVIEWED_FIXTURE_IDENTITIES`.
    """
    [(_, pattern)] = [c for c in _IDENTITY_COLLECTORS if c[0].startswith("evidence ")]
    # Shape -> (source text, the EXACT identity the collector must pull from it). A
    # merely-truthy check (`found and all(found)`) is satisfied by an over-greedy
    # capture too -- e.g. a lookahead that swallows past the frontmatter's next line
    # would collect `Alpha\\nverified: 2026-01-01` instead of `Alpha` and still pass a
    # non-empty check, while corrupting every identity `_REVIEWED_FIXTURE_IDENTITIES`
    # is built from. Asserting the precise value closes that.
    seen = {
        'frontmatter, bare': ('Company: Alpha\\nverified: 2026-01-01', 'Alpha'),
        'frontmatter, quoted': (
            'Company: "Example Foundry"\\nverified: 2026-07-01', 'Example Foundry'),
        'dict/kwarg, double': (
            'fields={"Company": "Example Systems", "Best For": "x"}', 'Example Systems'),
        'dict/kwarg, single': (
            "fields={'Company': 'Example Telemetry'}", 'Example Telemetry'),
    }
    for shape, (text, expected) in seen.items():
        found = [_identity_of(v) for v in pattern.findall(text)]
        assert found == [expected], f"{shape} was not collected as {expected!r}: {text!r} -> {found!r}"

    unseen = {
        # An f-string interpolation whose own value is quoted: excluding `{` from every
        # alternative is what stops `{e[` reaching the roster as a bogus identity.
        'f-string interpolation': 'f\'Company: "{e["company"]}"\'',
        'bare template': 'f"Company: {company}"',
    }
    for shape, text in unseen.items():
        leaked = [v for v in (_identity_of(m) for m in pattern.findall(text))
                  if v and not _is_source_text(v)]
        assert not leaked, f"{shape} leaked {leaked!r} as a fixture identity"


def test_the_evidence_company_block_list_collector_sees_every_shape_it_claims_to():
    """The Company sibling of the Skills block-list collector (#168 Task 11 review): a
    genuine `Company:` block-list fixture -- the field most likely to carry a REAL
    EMPLOYER NAME, the single highest-value thing this whole file exists to catch -- was
    caught by nothing until this collector existed. Reuses `_evidence_block_list_re` and
    `_block_list_items` UNCHANGED (see their own docstrings): the promotion is wiring, a
    second `key` argument and a second call site, not a second regex.

    Mirrors `test_the_evidence_skills_collector_sees_every_shape_it_claims_to`'s coverage
    of the shared machinery, scoped to what is NEW here: the `Company` key specifically,
    a multi-item block list (Skills' own test already proves the boundary handling this
    key reuses unchanged), cross-key isolation, and the `_identity_of` slug-reduction
    `_collect_block_list` applies on top that the Skills path never needed
    (`_all_fixture_skill_values` reads raw values, not lead identities).

    Written as the collector sees it -- Python SOURCE fragments, `\\n` two characters,
    exactly as a real packed-frontmatter fixture writes it. This file is `_SELF`,
    excluded from `_test_sources()`, so none of these values reaches
    `_REVIEWED_FIXTURE_IDENTITIES`.
    """
    pattern = _COMPANY_BLOCK_LIST_COLLECTOR[1]

    # A two-item block list proves the extractor makes no single-item assumption for
    # this key, even though a REAL Company fixture would realistically name one employer.
    two_item = "Company:\\n  - Example Alpha\\n  - Example Beta\\nverified: 2026-01-01"
    assert _block_list_items(pattern, two_item) == ["Example Alpha", "Example Beta"]

    # The escaped-`\n` spelling every real evidence fixture in this repo actually uses.
    escaped = 'Company:\\n  - Example Foundry\\nverified: 2026-07-01'
    assert _block_list_items(pattern, escaped) == ["Example Foundry"]

    # A block list under a DIFFERENT key (Skills' own shape) must not leak into the
    # Company sweep -- a collector keyed loosely enough to match it too would attribute
    # a skill value to the employer roster instead.
    other_key = "Skills:\\n  - Example Query\\nverified: x"
    assert _block_list_items(pattern, other_key) == []

    # `_collect_block_list` (unlike `_all_fixture_skill_values`, which reads raw values)
    # reduces each item through `_identity_of` before it reaches the roster -- a
    # `Company - Role.md`-shaped block item must reduce the same way a filename or a
    # dict/kwarg value already does for the other five identity collectors.
    slug_item = "Company:\\n  - Example Foundry - Staff Engineer.md\\nverified: x"
    [raw_slug] = _block_list_items(pattern, slug_item)
    assert _identity_of(raw_slug) == "Example Foundry"

    # An f-string interpolation is refused at the ITEM regex itself, structurally, the
    # same way `_evidence_field_re`'s own value alternatives exclude `{` -- not merely
    # filtered afterward by `_is_source_text`. A quoted or bare `{company}` placeholder
    # inside a block list matches nothing, so it never reaches `_identity_of` at all.
    placeholder_item = 'Company:\\n  - "{company}"\\nverified: x'
    assert _block_list_items(pattern, placeholder_item) == []


@pytest.mark.parametrize("label,pattern", _COLLECTORS, ids=[c[0] for c in _COLLECTORS])
def test_every_collector_actually_finds_fixtures(label, pattern):
    """A collector that matches nothing makes EVERY check built on it VACUOUSLY green.

    This is the failure mode that matters. A regex silently stops matching (a helper is
    renamed, a quoting style changes), the sweep finds an empty set, and the guard reports
    success while covering nothing — "a search that finds nothing proves nothing." The floor
    is deliberately low: it pins that the position is still LIVE, not how many fixtures the
    repo happens to have.

    Where each collector's result GOES differs, and the split is what the counts below
    describe. The five `_IDENTITY_COLLECTORS` feed `_all_fixture_identities()` and the
    `_REVIEWED_FIXTURE_IDENTITIES` roster checks. The other two feed their own dedicated
    checks instead: "equal-opportunities values" feeds a token-SHAPE check
    (`test_every_equal_opportunities_fixture_value_is_an_obvious_synthetic_token`) and
    "candidate identity values" feeds a ROSTER ratchet of its own
    (`test_every_candidate_identity_fixture_value_has_been_reviewed`) — see `_COLLECTORS`'
    own comment for why neither is folded into the employer roster. Either way, an empty
    `_collect(pattern)` is what makes the downstream assertion pass with nothing to check.
    """
    assert len(_collect(pattern)) >= 2, (
        f"the {label!r} collector matched fewer than 2 fixtures across tests/ — its regex has "
        f"probably drifted from the code, which would make the check(s) built on it vacuous")


def test_the_collector_split_this_file_documents_is_the_split_it_has():
    """Pins the two counts the docstring above states, because prose cannot be falsified.

    That docstring described FIVE collectors and "the fifth" for exactly as long as it took
    somebody to add a sixth (CodeRabbit found it on PR #161, one round after the collector
    landed). A stale count in a file whose whole job is documenting its own sweep SCOPE is
    worse than a stale count elsewhere: it tells the next reader that something is covered
    when it is not, which is the same shape as the vacuous-sweep failure the test above
    exists to catch.

    This repo has been bitten by a stale prose count twice already (`core/paths.py`'s
    ingress sites), and both times the fix was a corrected number that could go stale
    again. Asserting it instead means the SEVENTH collector reddens here, next to the prose
    it falsifies, rather than shipping a quietly wrong claim.

    Both numbers, not just the total: the split -- how many feed the shared employer roster
    versus their own dedicated checks -- is the part the docstring actually explains, and a
    total alone would stay green if a collector moved from one group to the other.
    """
    assert len(_COLLECTORS) == 7, (
        f"{len(_COLLECTORS)} collectors, but the docstring of "
        "test_every_collector_actually_finds_fixtures describes seven -- update the prose "
        "and this number together")
    assert len(_IDENTITY_COLLECTORS) == 5, (
        f"{len(_IDENTITY_COLLECTORS)} collectors feed the employer roster, but that same "
        "docstring says five -- a collector moved between the two groups, so the prose "
        "explaining the split is now wrong")


def test_no_unreviewed_employer_name_in_test_fixtures():
    unreviewed = sorted(_all_fixture_identities() - _REVIEWED_FIXTURE_IDENTITIES)
    assert not unreviewed, (
        "test fixtures introduce lead-identity values that nobody has reviewed:\n  "
        + "\n  ".join(unreviewed)
        + "\n\nThis repo is PUBLIC and these fixtures ship in it. Confirm each names no real "
          "firm — a local check CANNOT establish this, it needs a human or a web lookup — then "
          "add it to _REVIEWED_FIXTURE_IDENTITIES. Prefer `Example <Word>`.")


def test_the_reviewed_roster_carries_no_identity_the_fixtures_stopped_using():
    """A roster entry outliving its fixture is a name recorded for no reason.

    Completeness in the other direction, matching the `_SWEPT_CONFIGS` pattern in
    test_sluice_neutral_defaults.py. Without it the roster only ever grows, and a name deleted
    from the fixtures for a NEUTRALITY reason stays written down here — the same "the
    remediation records the value" trap the deletion was meant to close.
    """
    # Both sweeps, because both feed the roster: the four positional collectors AND the
    # narrow CV-body one at the bottom of this file. Checking only the first would report
    # every CV-fixture identity as stale the moment it was reviewed -- the reverse check
    # marking a name unused while a fixture is actively using it.
    in_use = _all_fixture_identities() | _cv_fixture_identities()
    stale = sorted(_REVIEWED_FIXTURE_IDENTITIES - in_use)
    assert not stale, (
        "these values are on the reviewed roster but no fixture uses them any more:\n  "
        + "\n  ".join(stale)
        + "\n\nDelete them from _REVIEWED_FIXTURE_IDENTITIES.")


# `_REVIEWED_FIXTURE_IDENTITIES` is a roster somebody has LOOKED AT -- that only works for a
# category small enough to review by hand, one name at a time. The twelve equal-opportunities
# fields (`_EO_FIELDS`, above) are special-category personal data: ethnicity, religion, sexual
# orientation, disability, gender identity and similar. Nothing running locally can tell a real
# demographic category from an invented one -- "British Sikh" and "SYNTHETIC-ETHNICITY-1" are
# indistinguishable to a regex except in the one property that matters: only one of them is
# reviewable by a human at a glance without needing to already know the answer. So this is a
# SHAPE ratchet, not a roster: every value a fixture puts in a warned-field position must look
# like SYNTHETIC-<FIELD>-<N>, full stop, rather than being individually eyeballed and
# allow-listed the way `_REVIEWED_FIXTURE_IDENTITIES` works. `tests/test_apply_packet.py`'s own
# `_SYNTHETIC_WARNED` dict carries a comment stating its values are synthetic placeholders --
# but a comment stating that is not, by itself, a check: it says nothing about what happens the
# day somebody edits one of those values to something that reads more naturally and forgets the
# token shape. The tests below are what turn that stated intent into something enforced.
_SYNTHETIC_TOKEN = re.compile(r"^SYNTHETIC-[A-Z_]+-\d+$")


def test_every_equal_opportunities_fixture_value_is_an_obvious_synthetic_token():
    """Nothing local can tell a real demographic category from an invented one, so the token
    SHAPE is what makes this reviewable -- the same ratchet logic as
    `_REVIEWED_FIXTURE_IDENTITIES`, applied to a category that roster does not cover (see its
    own comment above `_SYNTHETIC_TOKEN`).

    A prose comment stating fixture values are synthetic is not a check.
    """
    found = _collect(_EO_PATTERN)
    # Scope pin, asserted BEFORE the shape check below -- `all([])` is `True`, so a shape
    # check alone cannot tell "every value is clean" from "the pattern stopped matching
    # anything." The floor here is exact, not merely "at least 2" the way the sibling
    # anti-vacuity test is: today's tests/ has exactly one literal fixture per `_EO_FIELDS`
    # member (`tests/test_apply_packet.py`'s `_SYNTHETIC_WARNED` dict, 12 entries, plus one
    # `ethnicity=` kwarg in `tests/test_candidate.py`'s `has_any_declared` tests, whose value
    # duplicates `_SYNTHETIC_WARNED["ethnicity"]` and so contributes no NEW unique value to
    # this set). `test_every_passthrough_field_reaches_both_the_packet_and_render_text`
    # (`tests/test_apply_packet.py`) separately builds 28 further tokens -- one per
    # `_PASSTHROUGH_KEYS` field, not just the 12 warned ones -- via a dict comprehension keyed
    # on a loop variable, not a literal; every one of those 28 is COMPUTED from its own field
    # name at test-collection time, so it cannot drift the way a hand-typed literal can, and
    # `_EO_PATTERN` deliberately does not match it (no literal field name appears in that line
    # for a regex to see). See
    # test_the_equal_opportunities_pattern_does_not_match_a_computed_fstring_token below, which
    # pins that exemption directly rather than leaving it to this comment. If this count ever
    # changes, read what changed before updating the number: reuse the CANONICAL
    # SYNTHETIC-<FIELD>-<N> value for an existing field rather than inventing a second one for
    # it, add a genuinely new field's own fixture and let the count grow by exactly one, or --
    # if the count itself should change for some other reason -- rewrite this assertion
    # deliberately rather than padding it with a suppressed exception (the same "a guard fires
    # on good fixtures and gets suppressed" trap the module docstring names for
    # `_REVIEWED_FIXTURE_IDENTITIES`).
    assert len(found) == len(_EO_FIELDS), (
        f"expected exactly {len(_EO_FIELDS)} literal equal-opportunities fixture values (one "
        f"per _WARNED_KEYS field) but found {len(found)}: {sorted(found)} -- see this "
        f"assertion's own comment for how to resolve a genuine change")
    bad = sorted(v for v in found if not _SYNTHETIC_TOKEN.match(v))
    assert not bad, (
        "equal-opportunities fixture values must look like SYNTHETIC-<FIELD>-<N>:\n  "
        + "\n  ".join(bad))

    # ...and now the same claim FIELD-BY-FIELD, which the value-set assertions above
    # cannot make (CodeRabbit, PR #161). `_collect` returns a SET of values, so a count of
    # 12 proves only that twelve distinct values exist somewhere -- not that each warned
    # field has one, nor that a field's value carries that field's own token. Two shapes
    # pass everything above while defeating the intent: `ethnicity` carrying two distinct
    # tokens while `religion` carries none, and a straight permutation
    # (`ethnicity="SYNTHETIC-RELIGION-1"`). The second is the one that matters -- the whole
    # point of the token convention is that a reader can see at a glance which field a
    # value belongs to, and a permuted token reads as correct while pointing at the wrong
    # field.
    by_field = {}
    for text in _test_sources():
        for m in _EO_PAIR_PATTERN.finditer(text):
            value = _identity_of(m.group(3))
            if _is_source_text(value):
                continue
            by_field.setdefault(m.group(1) or m.group(2), set()).add(value)

    missing = sorted(set(_EO_FIELDS) - set(by_field))
    assert not missing, (
        f"warned field(s) with no literal fixture value anywhere in tests/: {missing} -- add "
        "one (SYNTHETIC-<FIELD>-<N>) so the field is actually covered, rather than relying on "
        "another field's spare value to make the count up")
    mismatched = sorted(
        f"{field}={value!r}"
        for field, values in by_field.items()
        for value in values
        if value != f"SYNTHETIC-{field.upper()}-1")
    assert not mismatched, (
        "each warned field's fixture value must be its OWN canonical token, "
        "SYNTHETIC-<FIELD>-1 -- a permuted or duplicated token passes every value-set check "
        "above while pointing a reader at the wrong field:\n  " + "\n  ".join(mismatched))


def test_the_equal_opportunities_pattern_does_not_match_a_computed_fstring_token():
    """Pins an exemption executably rather than in prose: the dict comprehension
    `test_every_passthrough_field_reaches_both_the_packet_and_render_text`
    (`tests/test_apply_packet.py`) uses to build 28 SYNTHETIC-<FIELD>-1 tokens --
    `{name: f"SYNTHETIC-{name.upper()}-1" for name in packet._PASSTHROUGH_KEYS}` -- has no
    literal field name in it for a regex to see (`name` is a loop variable, not a string
    literal, at every position `_EO_PATTERN` looks).

    What this genuinely pins is narrower than "the comprehension is safe": it is that
    `_EO_PATTERN`, as currently written, does not match this exact shape -- so a future
    WIDENING of the pattern (to close some other gap) that starts swallowing computed
    f-string tokens is caught here, at the 12-value scope pin above, before it silently
    inflates to 28. Rewriting the comprehension ITSELF with a literal key is caught by that
    scope pin directly instead (a new literal value changes `found`'s size) -- not by this
    test, whose `sample` is a frozen hand-copy unaffected by edits elsewhere.
    """
    sample = ('declared = {name: f"SYNTHETIC-{name.upper()}-1" '
              'for name in packet._PASSTHROUGH_KEYS}')
    assert _EO_PATTERN.findall(sample) == []


def test_the_equal_opportunities_pattern_recognizes_all_four_fixture_shapes():
    """`_EO_PATTERN` widened from two fixture shapes to four (double/single-quoted
    dict-literal, double/single-quoted kwarg, YAML frontmatter quoted/bare) after a review
    round measured that the original two-shape version silently missed the other two --
    both live conventions in this repo, not hypothetical. Pins each shape directly, plus the
    two guards that make the widening safe: a `sub_ethnicity=` kwarg must still be rejected
    (the identifier-suffix collision the boundary guard exists for), and a YAML value
    immediately following the PRECEDING key's `\\n` line-break escape -- as it appears in
    Python SOURCE TEXT, not the rendered note -- must still be recognized, because the
    character before the next key is then the word character `n`, indistinguishable at one
    character of lookback from the `_` in `sub_ethnicity`. `_EO_BOUNDARY`'s own comment
    explains the mechanism; this test is what proves both sides of it hold at once.
    """
    assert _EO_PATTERN.findall('"ethnicity": "SYNTHETIC-ETHNICITY-1"') == [
        '"SYNTHETIC-ETHNICITY-1"']
    assert _EO_PATTERN.findall("CandidateProfile(ethnicity=\"SYNTHETIC-ETHNICITY-1\")") == [
        '"SYNTHETIC-ETHNICITY-1"']
    assert _EO_PATTERN.findall("'ethnicity': 'SYNTHETIC-ETHNICITY-2',") == [
        "'SYNTHETIC-ETHNICITY-2'"]
    assert _EO_PATTERN.findall("CandidateProfile(ethnicity='SYNTHETIC-ETHNICITY-2')") == [
        "'SYNTHETIC-ETHNICITY-2'"]
    assert _EO_PATTERN.findall('ethnicity: "SYNTHETIC-ETHNICITY-3"') == ['"SYNTHETIC-ETHNICITY-3"']
    assert _EO_PATTERN.findall("ethnicity: SYNTHETIC-ETHNICITY-4") == ["SYNTHETIC-ETHNICITY-4"]
    # The preceding key's `\n` escape, as it appears in Python source -- not an actual newline.
    escaped = ('profile_fm = \'town: "Example Town"\\n'
               'ethnicity: "SYNTHETIC-ETHNICITY-5"\\n\'')
    assert _EO_PATTERN.findall(escaped) == ['"SYNTHETIC-ETHNICITY-5"']
    # The identifier-suffix collision the boundary guard exists to reject.
    assert _EO_PATTERN.findall('sub_ethnicity="Real Value"') == []


# RFC 2606 / RFC 6761 reserve these for documentation and testing; they can never resolve to a
# real host, so an address built from one cannot name a real employer's mail domain.
#
# Split in two because the matching rules differ, and conflating them punched a hole in this
# guard: a plain `endswith("example.com")` also accepts `notexample.com`, which is an ordinary
# registrable domain that could belong to an employer. The reserved TLDs are safe under
# endswith because their leading dot forces a real label boundary; the reserved DOMAINS must
# match exactly or as a dot-delimited subdomain.
_RESERVED_TLDS = (".invalid", ".example", ".test", ".localhost")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")


def _is_reserved(domain: str) -> bool:
    if domain.endswith(_RESERVED_TLDS):
        return True
    return any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS)

# Non-employer real domains. Each needs a reason, not just an entry.
_DOMAIN_ALLOWLIST = {
    # The repo's own commit-trailer identity, asserted on by tests/test_packaging.py. A GitHub
    # noreply address, not an employer.
    "users.noreply.github.com",
}

_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')


def test_every_email_domain_in_test_fixtures_is_reserved_or_allowlisted():
    """The broader net: a mail domain is the shape an employer identity most often hides in.

    `Acme` reads as obviously fake. `jobs@<something>.com` does not, and a plausible-looking
    placeholder domain is usually registered by somebody — this guard's first run found
    `jobs@company.com` sitting in a track fixture.
    """
    offenders = set()
    for text in _test_sources():
        for domain in _EMAIL.findall(text):
            d = domain.lower().rstrip(".")
            if d in _DOMAIN_ALLOWLIST or _is_reserved(d):
                continue
            offenders.add(d)
    assert not offenders, (
        "test fixtures use mail domains that are not reserved for documentation:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nUse an RFC 2606 reserved domain (`example.com`, or anything under `.invalid` / "
          "`.example`) so the address cannot name a real host.")


def test_a_lookalike_domain_is_not_mistaken_for_a_reserved_one():
    """`notexample.com` is registrable and could belong to an employer.

    The first cut of this guard tested `d.endswith("example.com")`, which accepts it. A
    neutrality check that silently passes an employer domain is worse than none, because it
    reads as coverage. The reserved TLDs are still endswith-matched: their leading dot forces
    a real label boundary, so `.invalid` cannot be spoofed the same way.
    """
    assert _is_reserved("example.com")
    assert _is_reserved("careers.example.com")
    assert _is_reserved("anything.invalid")
    assert _is_reserved("evil.example")
    assert not _is_reserved("notexample.com"), "lookalike accepted as reserved"
    assert not _is_reserved("example.com.evil.co.uk"), "suffix-spoofed domain accepted"
    assert not _is_reserved("myexample.org")
    assert not _is_reserved("indeed.com")


def test_the_email_sweep_actually_reads_fixtures():
    """Same vacuity guard as above: an `_EMAIL` regex that matches nothing passes silently."""
    found = {m.lower() for text in _test_sources() for m in _EMAIL.findall(text)}
    assert len(found) >= 10, (
        f"the email sweep found only {len(found)} domains across tests/ — the regex has "
        f"probably drifted, which would make the reserved-domain check vacuous")


# ── candidate identity: classification completeness, then the ratchet ─────────

def test_every_candidate_profile_field_is_classified():
    """The forcing function that keeps the two rosters honest as the dataclass grows.

    A new `CandidateProfile` field is invisible to every sweep in this file until someone
    puts it in a bucket, and invisible-by-default is precisely how a person-data position
    ships unswept -- which is how the gap this collector closes came to exist. Derived
    from `dataclasses.fields`, never a hand-typed count, so it cannot go stale the way a
    number in prose does.

    A field may sit in exactly one bucket. Overlap is a real hazard rather than a
    tidiness rule: `_EO_FIELDS` is IMPORTED from `packet.py`, so a future
    reclassification there could move a field into it while a copy of the name also sat
    in `_PERSON_DATA_FIELDS` here -- and then the value would be checked against the
    wrong convention (a reviewed-roster name where a SYNTHETIC-<FIELD>-<N> token is
    required, or the reverse) with nothing red.
    """
    declared = {f.name for f in dataclasses.fields(CandidateProfile)}
    buckets = {
        "_EO_FIELDS": set(_EO_FIELDS),
        "_PERSON_DATA_FIELDS": set(_PERSON_DATA_FIELDS),
        "_NON_IDENTIFYING_FIELDS": set(_NON_IDENTIFYING_FIELDS),
    }
    classified = set().union(*buckets.values())

    assert not (declared - classified), (
        "unclassified CandidateProfile field(s) -- put each in _PERSON_DATA_FIELDS (free-text "
        "identity: name, contact channel, postal address) or _NON_IDENTIFYING_FIELDS (a yes/no, "
        f"a closed set, or a sluice SETTING): {sorted(declared - classified)}")
    assert not (classified - declared), (
        "this file classifies field(s) CandidateProfile no longer declares -- a renamed or "
        f"removed field leaves its collector matching nothing: {sorted(classified - declared)}")
    for a, b in itertools.combinations(sorted(buckets), 2):
        assert not (buckets[a] & buckets[b]), (
            f"{a} and {b} both claim {sorted(buckets[a] & buckets[b])} -- a field in two "
            "buckets is checked against two different value conventions")


def test_every_candidate_identity_fixture_value_has_been_reviewed():
    """The ratchet. Nothing local can tell a real forename, postcode or mobile number from
    an invented one, so this cannot classify -- it can only refuse to let a NEW value into
    a candidate identity position without a human writing it into
    `_REVIEWED_CANDIDATE_VALUES` and saying, in that roster's comment, on what ground.

    Same shape and same limits as the employer-identity ratchet above; the difference is
    the roster it polices and the positions it watches. It is deliberately NOT folded into
    `_REVIEWED_FIXTURE_IDENTITIES`: that roster is documented as being about LEAD
    identities (company names, board slugs), and reviewing `Ada` as though it were a
    candidate employer would blur a distinction this file already goes to some length to
    keep.
    """
    found = _collect_person_data()
    # Scope pin FIRST -- `all([])` is `True`, so the review check below cannot tell "every
    # value is reviewed" from "the pattern stopped matching anything".
    #
    # DERIVED from the roster's own keys, not a number. The previous version was a floor
    # (`>= 6`) with the field names spelled out beside it, and it went stale the moment
    # `date_of_birth` and `first_language` joined `_PERSON_DATA_FIELDS` -- caught by
    # CodeRabbit on PR #161, one round after the same class was caught in the collector
    # docstring above. Fixing that one by pinning its counts closed the instance and not
    # the class, which is the recurring shape here. So this states no count at all: every
    # field the roster has reviewed values FOR must still be found by the sweep, which is
    # exact, self-updating, and cannot be wrong about how many fields there are.
    #
    # One direction only, deliberately. A rostered field the sweep stops finding means the
    # PATTERN drifted (or the fixture went away), and that is what makes this a scope pin.
    # The other direction -- a field the sweep finds that the roster does not cover -- is
    # not an error here: `.get(field, frozenset())` below reports every one of its values
    # as unreviewed, which is the more actionable message and names the values themselves.
    vanished = sorted(set(_REVIEWED_CANDIDATE_VALUES) - set(found))
    assert not vanished, (
        f"the candidate-identity sweep no longer finds any fixture for {vanished} -- the "
        "roster has reviewed values for those fields, so either the pattern drifted and the "
        "sweep is now vacuous, or the fixtures were removed and the roster entries are dead")
    assert found, "the candidate-identity sweep matched nothing at all across tests/"

    # Subtracted PER FIELD. A flat roster would accept a reviewed forename as a postcode,
    # and the repo's commit-trailer address -- reviewed only as packaging metadata -- in
    # any candidate position at all.
    unreviewed = sorted(
        f"{field}={value!r}"
        for field, values in found.items()
        for value in values - _REVIEWED_CANDIDATE_VALUES.get(field, frozenset()))
    assert not unreviewed, (
        "unreviewed value(s) in a candidate identity position. Nothing here can tell a real "
        "person's data from an invented placeholder, so this needs a human: confirm each is "
        "synthetic and add it to _REVIEWED_CANDIDATE_VALUES under THAT FIELD (prefer the "
        "Example / example.invalid / reserved-phone-range conventions), or replace it.\n  "
        + "\n  ".join(unreviewed))


def test_the_derived_packaging_identity_is_narrow_and_present():
    """Both failure directions of `_packaging_author_emails`, which the ratchet alone
    cannot distinguish.

    EMPTY is caught elsewhere -- the address then reads as unreviewed and the ratchet
    reddens (witnessed). OVER-BROAD is not: a derivation that returned every string in
    `pyproject.toml`, or a whole author table rather than its email field, would silently
    widen the `email` roster to accept arbitrary values, and every test in this file would
    stay green while the guard stopped guarding. That is the same "reads as coverage"
    shape this file exists to prevent, so it gets its own check rather than relying on the
    ratchet to notice.

    Asserts SHAPE, not the value: writing the address into an assertion here would
    reintroduce the literal the derivation exists to remove.
    """
    got = _packaging_author_emails()
    assert got, (
        "the packaging author identity derived from pyproject.toml is empty -- either "
        "[project] authors lost its email, or the derivation is reading the wrong key")
    bad = sorted(v for v in got
                 if "@" not in v or any(c.isspace() for c in v) or "<" in v or ">" in v)
    assert not bad, (
        "the derivation must yield bare email addresses, one per author -- these are not "
        f"that shape, so it is reading something wider than the email field: {bad}")
    assert len(got) <= 4, (
        f"{len(got)} addresses derived -- this repo declares one author, so a jump here "
        "means the derivation widened to something other than [project] authors")


def test_the_candidate_roster_is_keyed_only_by_swept_fields():
    """A roster key naming a field the collector does not sweep reviews nothing.

    The mirror of the classification test above, for the roster rather than the buckets:
    move a field out of `_PERSON_DATA_FIELDS` and its roster entry silently becomes dead
    weight that still reads as coverage. Also catches a typo'd key, which would otherwise
    just mean every value under it goes unreviewed.
    """
    stray = sorted(set(_REVIEWED_CANDIDATE_VALUES) - set(_PERSON_DATA_FIELDS))
    assert not stray, (
        f"roster key(s) that no collector sweeps: {stray} -- either the field left "
        "_PERSON_DATA_FIELDS or the key is misspelled; both make the entry inert")


def test_the_candidate_identity_collector_recognizes_all_four_fixture_shapes():
    """Executable proof the pattern reaches every shape a person-data fixture is written
    in, plus the two shapes it must NOT reach. Without this, the sweep above could be
    matching one shape out of four and its scope pin would still pass.
    """
    shapes = [
        ('"surname": "Roe"', "surname", "Roe"),                    # double-quoted dict key
        ("surname='Roe'", "surname", "Roe"),                        # single-quoted kwarg
        ('surname="Roe"', "surname", "Roe"),                        # double-quoted kwarg
        ("'surname: Roe\\n'", "surname", "Roe"),                    # bare YAML frontmatter
        # the escape-tail case _EO_BOUNDARY exists for: the `n` ending a preceding `\n`
        # sits immediately before the key, and is indistinguishable at one character of
        # lookback from the `_` of an identifier suffix.
        ("'town: Example Town\\nsurname: Roe\\n'", "surname", "Roe"),
    ]
    for text, field, value in shapes:
        # finditer, not search: the escape-tail row below carries TWO fields and `search`
        # returns the first (`town`), which reddened this test on its own fixture rather
        # than on the pattern. Assert the expected pair is AMONG the matches.
        pairs = [((m.group(1) or m.group(2)), _candidate_value_of(m.group(3)))
                 for m in _PERSON_PAIR_PATTERN.finditer(text)]
        assert (field, value) in pairs, f"{text!r}: matched {pairs!r}, wanted {(field, value)!r}"

    # An identifier merely ENDING in a field name is not that field.
    assert not _PERSON_PAIR_PATTERN.search('maiden_surname="Roe"'), (
        "a longer identifier ending in a field name must not match")
    # And the measured false-positive shape: a Python expression, not a fixture value.
    for text in ("surname = cv_name.partition(", "linkedin: searches:"):
        m = _PERSON_PAIR_PATTERN.search(text)
        assert m is None or _NOT_A_FIXTURE_VALUE.search(_candidate_value_of(m.group(3))), (
            f"{text!r} is source, not a fixture -- _NOT_A_FIXTURE_VALUE must drop it")


def test_the_two_equal_opportunities_patterns_find_the_same_values():
    """`_EO_PATTERN` (one group, registered in `_COLLECTORS`) and `_EO_PAIR_PATTERN` (two,
    used by the token test below) are built by the same helper from the same field tuple,
    so they should agree by construction -- but "should, by construction" is the shape of
    claim this repo keeps finding to be false. Pinned rather than asserted in prose.
    """
    single = _collect(_EO_PATTERN)
    paired = {_identity_of(m.group(3))
              for text in _test_sources()
              for m in _EO_PAIR_PATTERN.finditer(text)}
    # Same predicate `_collect` applies to `single`, deliberately: filtering the two
    # sides differently would make them disagree over the FILTER rather than the patterns,
    # which is the one thing this test is not trying to measure.
    paired = {v for v in paired if not _is_source_text(v)}
    assert single == paired, (
        f"the two equal-opportunities patterns disagree: only in single={sorted(single - paired)}, "
        f"only in paired={sorted(paired - single)}")


# ---------------------------------------------------------------------------
# The JSON golden corpus (#27).
#
# Everything above sweeps `tests/**/*.py`. `_test_sources()` says so in as many words, and
# that is exactly how `tests/fixtures/*/raw.json` -- a corpus of CAPTURED board payloads --
# carried real employer names and a real hunt geography through a guard written to catch
# precisely them, for the repo's whole public life. A scrub before the first public commit had
# already replaced MOST of the company names with a fictional roster, so the corpus read as
# reviewed while the rest stayed real -- a partial scrub is indistinguishable from a complete
# one, which is the actual lesson. The values are not named here, deliberately: naming which
# captured strings were the real ones would make this file a sharper disclosure than the
# fixtures ever were, and it is the ONE file `_test_sources()` excludes (`p.name != _SELF`),
# so no sweep in this repo would ever see it.
#
# Captured payloads need their own machinery rather than another regex on `.py` text: the
# values are JSON, and the board's DOM jams company and location into one node with no
# separator ('Example Telemetry EdgePalmerburgh ZZ9Z'), which is a legitimate fixture and a
# entry in an IDENTITY roster. So the two halves keep separate rosters and separate reasons.
# ---------------------------------------------------------------------------

_CORPUS_DIR = _TESTS_DIR / "fixtures"

# Reviewed 2026-08-21 (#27). Every value here is SYNTHETIC by construction, not captured:
# the corpus was scrubbed through a one-to-one token substitution, so 'Ellery Kestrelburgh'
# and 'Allied Sundic Reaches' are invented toponyms occupying the exact token STRUCTURE the
# real values had. `Palmerburgh`, `Clarkefurt` and `Potterburgh` are reused from
# tests/test_leads_location.py, where they were already reviewed.
#
# Same standing rule as `_REVIEWED_FIXTURE_IDENTITIES`: adding an entry is a DECISION that it
# names no real place, and nothing running locally can establish that. `Remote` is on a
# different ground -- it is an arrangement, not a location, and `core/leads.py`'s `_REMOTE_ONLY`
# treats it as evidence of NO fixed location.
#
# This is deliberately NOT `conftest.py`'s `LOCATIONS = ("Alfa", "Bravo", "Charlie", "Foxtrot")`,
# and the difference is structural rather than stylistic. Those are GENERATED single tokens, which
# is all a generated fixture needs. A captured value carries internal structure the comparator is
# tested against -- 'X , Y - Country (ABBREV)', a bare city, that city plus an outcode -- and a
# NATO letter cannot carry any of it, so substituting one would change what `_compare_locations`
# sees and silently rewrite the evidence. Use `Alfa`/`Bravo` for anything generated; extend this
# family only for captured values whose SHAPE has to survive.
_REVIEWED_CORPUS_LOCATIONS = frozenset({
    # Added 2026-08-28 with wttj's recapture. A THREE-token comma list, because the
    # captured value was one -- a board listing a role in three cities. Each component
    # is already a reviewed invented toponym in this roster; the composite is new only
    # as an arrangement, and it preserves the token STRUCTURE `_norm_location` reduces
    # to a set, which is what a substitution here has to keep intact.
    'Palmerburgh, Potterburgh, Clarkefurt',
    'ASR', 'Allied Brennmark (Remote)', 'Allied Sundic Reaches - Allied Sundic Reaches',
    'Brackenburgh - Bantria', 'Clarkefurt', 'Clarkefurt - Allied Sundic Reaches',
    'Clarkefurt - Allied Sundic Reaches (ASR)', 'Ellery Kestrelburgh',
    'Ellery Kestrelburgh , Quillon Denfurt - Allied Sundic Reaches (ASR)',
    'Ellery Kestrelburgh - Allied Sundic Reaches',
    'Ellery Kestrelburgh - Allied Sundic Reaches (ASR)', 'Fennimoreburgh', 'Hensleyfurt',
    'Hensleyfurt - Halvenia', 'Hybrid work in Palmerburgh', 'Karnovia', 'Marshburgh',
    'Marshburgh - Norvane Thessary', 'Norvane Thessary - Norvane Thessary', 'Palmerburgh',
    'Palmerburgh Area, Allied Brennmark (Hybrid)', 'Palmerburgh ZZ9Z',
    'Palmerburgh, Wexmoor, Allied Brennmark (Hybrid)',
    'Palmerburgh, Wexmoor, Allied Brennmark (Remote)', 'Palmerburgh\xa0∙ Choose area',
    'Potterburgh', 'Potterburgh - Allied Sundic Reaches (ASR)', 'Remote',
    'Sedgewickfurt - Sedgewickfurt', 'Tolliverfurt', 'VSA', 'Vesperia', 'Whitlockfurt',
    'Wrenfieldburgh - Norvane Thessary',
})

# Reviewed 2026-08-21 (#27). EVERY employer in the captured corpus is `Example <Word>`, and that
# uniformity is the point rather than a style preference.
#
# The corpus arrived carrying a stock fictional-brand roster the pre-release scrub had installed,
# and real marks it had missed. Removing the ones anybody named did not settle anything: whether a
# given brand is also a live registered company cannot be decided locally, so each review round
# reached a different name and re-opened the question -- three separate rounds on this PR alone,
# the last of which turned up an active UK registration behind a name from the stock roster. That
# is an unbounded argument, and every round of it costs a review slot.
#
# `Example <Word>` ends it: no lookup can match it to a real firm, so the class is closed instead
# of the instance. This is NOT the general convention (see this module's docstring on why a
# repo-wide `Example <Word>` rule was rejected -- it would fire on ~40 legitimate `.py` fixtures);
# it is specific to the CAPTURED corpus, which is the one file class where the values came off a
# real board and nobody can vouch for them.
#
# The `<Company><Location>` entries are the boards' DOM jam captured verbatim;
# `sluice/ingest/base.py`'s `_demash_company` exists to split them, so they are the fixture the
# demash path is tested against.
_REVIEWED_CORPUS_COMPANIES = frozenset({
    'Example Analytics', 'Example Analytics WestHybrid work in Palmerburgh',
    'Example Analytics WestPalmerburgh', 'Example Bank', 'Example Brewing', 'Example Chemical',
    'Example Compression', 'Example Cybernetics', 'Example Dynamics', 'Example Enterprises',
    'Example Foods', 'Example Foundry', 'Example FoundryHybrid work in Palmerburgh',
    'Example Genetics', 'Example Grid', 'Example Imports', 'Example Interstellar',
    'Example Ironworks', 'Example IronworksHybrid work in Palmerburgh', 'Example Manor',
    'Example Meridian', 'Example MeridianPalmerburgh', 'Example Northgate', 'Example Retail',
    'Example Robotics', 'Example Systems', 'Example Systems ABMPalmerburgh',
    'Example Telemetry', 'Example Telemetry EdgePalmerburgh ZZ9Z', 'Example Towers',
    # Added 2026-08-28 with wttj's recapture, when that source moved from the Otta carousel
    # to WTTJ's list view and its fixture had to be taken again. All three were already on
    # `_REVIEWED_FIXTURE_IDENTITIES`; this roster is separate, so they needed reviewing here
    # too. The capture was scrubbed through a one-to-one map onto reviewed values before it
    # was written, so no real employer reached the tree at any point.
    'Example Cloud', 'Example Data', 'Example Tidal',
})


def _corpus_files():
    """Every captured-payload fixture. Sorted so the anti-vacuity check below can name them."""
    return sorted(_CORPUS_DIR.glob("*/raw.json"))


def _corpus_values(key):
    """Every distinct non-blank string under `key`, anywhere in any fixture.

    Recursive rather than keyed on a known envelope shape: the boards do not agree on one
    (`{"result": [...]}`, a bare list, a `{"jobs": {...}}` wrapper), and a walker keyed on
    the shapes that exist today silently stops seeing a source that changes its envelope --
    which for a NEGATIVE guard means it stops reporting rather than starts failing.
    """
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key:
                    # A list-valued key slipped past an `isinstance(v, str)` check entirely, and
                    # boards do return one. The digest still fired on it; the ROSTER did not,
                    # which is the half that produces a readable failure.
                    for item in ([v] if isinstance(v, str) else v if isinstance(v, list) else []):
                        if isinstance(item, str) and item.strip():
                            found.add(item)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in _corpus_files():
        walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def test_the_corpus_sweep_actually_reads_the_fixtures():
    """Anti-vacuity, and the reason this file's other sweeps carry one too.

    `all([])` is True and `set() - roster` is empty, so a walker that matches nothing passes
    every assertion below it while reporting a clean corpus. Pinning the SCOPE -- that the
    glob found the sources and the walk reached values in them -- is the only thing that
    distinguishes "nothing to report" from "not looking".
    """
    files = _corpus_files()
    assert len(files) >= 10, (
        f"the corpus glob found {len(files)} fixtures under {_CORPUS_DIR} -- it used to find "
        "16, so either the fixtures moved or the glob no longer matches them, and every "
        "corpus assertion below is now vacuous")
    assert _corpus_values("location"), "the walker reached no location values"
    assert _corpus_values("company"), "the walker reached no company values"
    # And pin the SCOPE, not merely a floor. `>= 10` cannot tell 16 fixtures from 11, and the
    # glob is hardcoded to `*/raw.json` -- so a `page2.json`, a nested search directory or a
    # second capture under any other name would sit in no roster, in no digest, and trip
    # nothing. That is #27's own shape ("nothing walked it") one level down, which is the last
    # mistake this file should repeat.
    every_file = sorted(p for p in _CORPUS_DIR.rglob("*") if p.is_file())
    assert every_file == files, (
        "files under tests/fixtures/ that the corpus sweep does not read:\n  "
        + "\n  ".join(str(p.relative_to(_TESTS_DIR)) for p in every_file if p not in set(files))
        + "\n\nEither fold them into _corpus_files() or move them out of the fixture tree.")


def test_no_unreviewed_location_in_the_json_golden_corpus():
    unreviewed = _corpus_values("location") - _REVIEWED_CORPUS_LOCATIONS
    assert not unreviewed, (
        "the golden corpus carries location values nobody has reviewed:\n  "
        + "\n  ".join(sorted(map(repr, unreviewed)))
        + "\n\nThis repo is PUBLIC and these fixtures ship in it. A captured payload records "
          "where the person running the scrape was looking for work, and the SET of them is a "
          "sharper disclosure than any one value (#27). Confirm each names no real place, "
          "then add it to _REVIEWED_CORPUS_LOCATIONS.")


def test_no_unreviewed_company_in_the_json_golden_corpus():
    unreviewed = _corpus_values("company") - _REVIEWED_CORPUS_COMPANIES
    assert not unreviewed, (
        "the golden corpus carries company values nobody has reviewed:\n  "
        + "\n  ".join(sorted(map(repr, unreviewed)))
        + "\n\nThis repo is PUBLIC and these fixtures ship in it. Confirm each names no real "
          "firm -- a local check cannot -- then add it to _REVIEWED_CORPUS_COMPANIES.")


@pytest.mark.parametrize("key,roster", [
    ("location", _REVIEWED_CORPUS_LOCATIONS),
    ("company", _REVIEWED_CORPUS_COMPANIES),
], ids=["location", "company"])
def test_the_corpus_rosters_carry_no_value_the_fixtures_stopped_using(key, roster):
    """The other direction, for the same reason the evidence script asserts both.

    A roster that outlives its fixtures is how a value removed FOR A NEUTRALITY REASON gets
    quietly re-added later: the entry is still sitting there saying somebody approved it, and
    nobody remembers that the approval was for a value that has since been scrubbed.
    """
    stale = roster - _corpus_values(key)
    assert not stale, (
        f"_REVIEWED_CORPUS_{key.upper()}S names values no fixture uses any more:\n  "
        + "\n  ".join(sorted(map(repr, stale)))
        + "\n\nDrop them, so the roster stays a list of things that are actually shipping.")


# The two rosters above are keyed on `location` and `company`, which are ENUMERABLE: a fixed
# vocabulary, small enough to list and read. `title` is not. It is free text, 223 distinct
# values of legitimate role wording -- and it carried geography anyway, in the shape
# `<role> <employer> <City, Region> ▪ <City, Region>`, because the boards append the posting's
# location to the role. (Described rather than quoted, for the reason two paragraphs down.)
# Rostering 223 role strings to catch that
# would be a wall nobody reads; a gazetteer of real place names would be BOTH a classifier
# this file's own docstring argues against AND a leak in its own right -- writing the removed
# values into `tests/` to forbid them puts them right back in the public tree.
#
# So: a digest per source over its whole payload -- every value and every key, not a sampled
# subset. It cannot say WHICH value is new, and that is the accepted trade -- it is a "go and look" gate
# on a corpus that has changed four times in the repo's life, and "go and look" is exactly the
# human judgement the rosters exist to force. It hashes canonical JSON of the PARSED payload
# with sorted keys, not the file's bytes and not a set of its strings: that keeps it blind to
# formatting and to key ORDER while still seeing record count, key names, numbers, booleans and
# REPETITION -- a set loses multiplicity, and moving one row between two values already present
# in the same fixture left the earlier version byte-identical with both rosters green.
_REVIEWED_CORPUS_DIGESTS = {
    'bayt':
        '01abe7de3090b9d8de40434f3d98927f7d2d6ef4cf79ee7a1650b99619046da4',
    'cord':
        '1cab7aefb74a83ab4fcb6b17495f69771cecaafd9d6310535ce96d8ef0985c7c',
    'cwjobs':
        '470c11b61a5a985d325867555d9f2af164fe94a33665c758963df16bc99e5b99',
    'eighty_k':
        '1f80ed92b4206af2c5e43744573ba2b661f5bb85636998021caf0898e82a9151',
    'google':
        'f7e8f40abc8dc145d5e964dedcfcad534e1c871f57b7bc04d8f80e9064ae8888',
    'gulftalent':
        '850bb0598f1c6f748f8a22ce236de69e5c1d8d7c6ad7460b12dace3e44d789ad',
    'indeed':
        'f08cbfc5f056b661e61a31adec649211a61ea4170fc0809fef3fed686ab0e476',
    'jobserve':
        '9518fadcc1bbe565f11012ec386eb7a2e751687cf7141af7a2bca5ae7f20e3f3',
    'linkedin':
        '839b676605277aba0a407d41c976355e558e5314f6aa4021ee7a1a3a4ae7d416',
    'naukrigulf':
        'b46e8ad4b6457dbb42fe4be9a6e91af31c4019a59cc4b4ae87c63f7ea7b789d0',
    'reed':
        'e6741736fd37d865a8e5daa7ad0d59aa389d73e174830eea3e43ea49af3cc1b7',
    'remoteok':
        '20eae5095dcd77aadc0a28b948f5ed199f3f3fc63ff24a84d83684f90afbe30b',
    'totaljobs':
        '43f361f02ede5efc9834fc4cc6661e035ef00c29a3fd197c16e963e28370937c',
    'wellfound':
        'c977ff49c8088fa58b774d9a4b2b1c98553bcd42817b1b2d801942149a12d20a',
    'weworkremotely':
        '880e0707532ccb3171d729f90b0d9a9997c34090b028d0a44ed398eeda7546c0',
    'wttj':
        'a3d2f62ae8285c16e1a43e4d0ba83a2f9343c8ff8d2729ebec0846fcb3ca659c',
}


def _corpus_digest(path):
    """A stable fingerprint of one fixture's whole parsed payload.

    Canonical JSON of the PARSED object, not a set of its string values. The set version lost
    MULTIPLICITY and structure: moving one row's company from one already-present value to
    another left the digest byte-identical while both rosters stayed green, because neither the
    value set nor the roster changed -- reproduced against a real fixture before this was
    rewritten. Sorting keys keeps it independent of key ORDER and of file formatting (re-indent
    a fixture and nothing moves), while record count, key names, numbers, booleans and
    repetition all now reach the hash.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def test_every_captured_fixture_matches_its_reviewed_digest():
    actual = {p.parent.name: _corpus_digest(p) for p in _corpus_files()}
    assert actual == _REVIEWED_CORPUS_DIGESTS, (
        "the captured-payload corpus changed.\n\n"
        "This gate is not about the change being wrong -- it is about a PUBLIC repo, where a "
        "fixture recaptured from a live board arrives carrying whatever that board printed: "
        "employer names, and the posting's location appended to the role title, which is the "
        "shape #27 was filed for and the one no roster in this file can enumerate.\n\n"
        "Read the diff for the sources below. Confirm no value names a real firm or a real "
        "place -- nothing running locally can establish either -- then paste the new digest "
        "into _REVIEWED_CORPUS_DIGESTS.\n\n"
        + "\n".join(
            f"  {src}: {_REVIEWED_CORPUS_DIGESTS.get(src, '(new source)')} -> {dig}"
            for src, dig in sorted(actual.items())
            if _REVIEWED_CORPUS_DIGESTS.get(src) != dig)
        + "".join(f"\n  {src}: (fixture removed, drop the entry)"
                  for src in sorted(set(_REVIEWED_CORPUS_DIGESTS) - set(actual))))


# ── CV-body employer lines (#167): a NARROW ratchet for the gap named in the docstring ──

# CV-domain modules whose FILENAME does not start `test_cv_`, so the glob below cannot
# find them. Hand-listed of necessity -- but this is now the only hand-list, and it holds
# the exceptions rather than the rule, which is the part that kept going stale.
_CV_MODULES_NOT_MATCHING_THE_CONVENTION = (
    "test_slop_phrase_retirement.py",     # #181
    "test_renderer_template.py",          # CV-body employer/education identities
    "test_onboard_questions.py",          # an employer fixture probing the gate
)

# Derived, not enumerated (#174). Twice a CV module was added and nobody remembered to
# list it, the second time being the module this very change put new fixtures in.
_CV_TEST_MODULES = tuple(sorted(
    {p.name for p in _TESTS_DIR.glob("test_cv_*.py")}
    | set(_CV_MODULES_NOT_MATCHING_THE_CONVENTION)))

# `Example <Word>` literals only. Weaker than the four positional collectors ON PURPOSE and
# stated as such above: this can ratchet a name that already LOOKS synthetic, which is not
# the same as catching a real one. It exists because #167 put employer identities into CV
# body prose, where no positional collector reaches them, and an unreviewed value there was
# previously invisible to every check in this file.
_CV_IDENTITY_RE = re.compile(r"\bExample [A-Z][A-Za-z]+")

# Skill values (#168) are NOT lead identities, and they get their own roster rather than
# joining `_REVIEWED_FIXTURE_IDENTITIES`. That roster's own docstring scopes it to "LEAD
# identities -- employers a fixture names", and `_CV_IDENTITY_EXEMPT` below exists (owner's
# ruling, 2026-08-24) precisely to keep a product-shaped NON-employer value off it:
# "rostering it would make the roster mean something wider than it says." Adding technology
# names by policy is what that carve-out was created to prevent -- one list answering two
# different questions, with no way to tell afterwards which call an entry records.
#
# Same tool, separate question, following the `_REVIEWED_CANDIDATE_VALUES` precedent. The
# question a human is being asked here is:
#
#   Is this technology-shaped value INVENTED, or does it name a real product or language
#   the candidate actually works with?
#
# Nothing running locally can answer that -- no local check can tell an invented name from
# a real one. This is a RATCHET: it forces the answer once, in the commit that introduces
# the value, rather than retroactively over an accumulated corpus.
#
# Defined HERE, above `_CV_IDENTITY_EXEMPT`, so that set can be DERIVED from this one
# rather than hand-listing the same values a second time -- see it immediately below.
_REVIEWED_SKILL_VALUES = frozenset({
    # Invented for #168, not drawn from any real skill inventory, and shaped to the
    # `Example <Word>` convention this file's own failure message prescribes.
    "Example Framework",
    "Example Query",
    # Invented for #168's SKILL_TOKEN_RE guard (tests/test_cv_bundle.py): the digit is
    # deliberately INSIDE a letter-led token, the one shape that rule must accept rather
    # than refuse.
    "Example Widget3",
    # Invented for #168 Task 4 (tests/test_cv_engine.py's STYLE-tier scoping test): must
    # match slop._PHRASES' "synergy" stem AND, once row 2 (containment) reaches the
    # SKILLS region, be a genuinely SOURCED skill -- one value doing both jobs, neither
    # of which the other three reviewed values can stand in for.
    "Example Synergy",
    # Invented for #168 Task 10 (tests/test_doctor.py's skills-reconciliation fixtures
    # and tests/test_evidence_cli.py's `experience list` Skills: surfacing test): a
    # name deliberately absent from any Skills Inventory fixture in the same test, so
    # the "entry Skills: absent from the inventory" NOTICE row has something real to
    # fire on.
    "Example Ghost",
    # Invented for the SAME Task 10 fixtures: the matching half, paired with a Skills
    # Inventory entry titled "example-widget" (`evidence_slug("Example Widget")`) so
    # the "inventory skill evidenced by no entry" row has a genuine non-firing case to
    # contrast against.
    "Example Widget",
    # Invented for #168 Task 10's own report-neutrality test
    # (`test_the_report_names_no_skill_string`): a name deliberately absent from the
    # inventory fixture in the same test, distinct from every other reviewed value so
    # a leak of THIS value specifically proves the report is not merely omitting one
    # already-rostered name by coincidence.
    "Example Zephyr",
    # NOT a name -- an all-punctuation `Skills:` value invented for #168 Task 10's
    # `evidence_slug`-cannot-reduce fixtures (tests/test_doctor.py). Synthetic noise,
    # reviewed the same as every other captured value: nothing here could ever be
    # mistaken for a real product name.
    "###",
    # ---- Reviewed 2026-08-28, when the AST collector below first reached them --------
    #
    # Every value under this heading was ALREADY in `tests/` and swept clean, in a shape
    # no colon-keyed regex could see (a `dict(Skills=...)` kwarg, a `parametrize` list, a
    # CV fixture's own emitted SKILLS section). None is a new fixture; what is new is that
    # the ratchet can now see them, which is the whole point of extending it.
    #
    # Placeholder and invented values first.
    #
    # `Examplestore3` -- invented for the trailing-period tokeniser regression: a
    # digit-bearing product-shaped name that ends a sentence in a bullet. `Example` family.
    "Examplestore3",
    # Self-describing invented label, used as the fabricated skill a grouped SKILLS
    # section smuggles past row 2. It names nothing and is not meant to.
    "Totally Invented Skill",
    # Single generic English words, used for what their SHAPE proves rather than for what
    # they name: `Widget` pins row 1's case sensitivity (`widget` must not fire) and, on
    # its own, that a dotted name is a NAME and not a prefix (`Widget` alone must be
    # UNSOURCED where `Widget.Node` is sourced). `Framework Widget` / `Widget Framework`
    # are the same two words in both orders, which is exactly what makes them a
    # subsequence-vs-set fixture.
    "Widget",
    "Framework Widget",
    "Widget Framework",
    # NOT names -- the malformed-value parametrize rows for `SKILL_TOKEN_RE`
    # (tests/test_cv_bundle.py). Every one is a number or a number-led token, which is the
    # single property under test.
    "92",
    "92x",
    "120ms",
    "Result 92",
    "Example 92",
    #
    # ---- Token SHAPES for the `SKILL_TOKEN_RE` rule, all synthetic --------------------
    #
    # This group used to hold real public technology and standard names (`.NET`,
    # `Node.js`, `ISO 9001`, `Web 2.0`, `Section 508`, `3D modelling`, `5S`, `802.11ac`,
    # `Kubernetes`) under an exemption arguing a real name was needed to prove the rule
    # costs real things. Owner's ruling on the #168 review round: replace them here, keep
    # them in `docs/USAGE.md`. A synthetic value carries every shape the rule turns on at
    # no cost, and `docs/USAGE.md` is where a user needs to read WHICH of their own
    # credentials the over-refusal costs them -- so the information stays exactly where
    # it is load-bearing and leaves the corpus where it was only decoration.
    #
    # Accepted by the rule -- a leading dot before a letter, alone and with a second word:
    ".Example",
    ".Example Widget",
    # ...and an internal dot, whose leading token is deliberately one that appears nowhere
    # else in that fixture's bundle, so `Widget` alone can prove the prefix half.
    "Widget.Node",
    # Refused by the rule, every one because a token leads with a DIGIT: a standard's
    # number after a word, a dotted version after a word, a digit-led token opening a
    # phrase, a digit-led token alone, and a digit-led token carrying internal dots.
    "Example 9001",
    "Example 2.0",
    "9E modelling",
    "5X",
    "123.45ab",
    # An emitted skill in a row-2 containment fixture, present in the CV and absent from
    # the bundle, so the gate must call it UNSOURCED. Self-describing and invented.
    "Example Unbacked",
    #
    # ---- Added by the re-review round's own fixtures (2026-08-28) --------------------
    #
    # The ratchet caught these three on its first run after the AST collector went in --
    # written in this round, unrostered, red. Recorded here as the human call they force.
    #
    # `Example Query` with its two words REVERSED: the fixture for row 2 matching a token
    # SUBSEQUENCE across a sentence seam. It names nothing; the word order IS the test.
    "Query Example",
    # NOT names -- the token-less `Skills:` values that must be REFUSED rather than
    # silently switching row 1's abstain off. `#` is here because `_WORD_RE` admits it (so
    # `C#` survives), which sends it down the letter-leading arm instead.
    "...",
    "#",
    #
    # ---- Added by #257's line-shape fixtures (2026-09-04) ---------------------------
    #
    # A CATEGORY LABEL prefixed onto an already-rostered value, for the row 2 fixture
    # proving a labelled line is refused even when it names a single REAL skill (which is
    # what establishes that the defect is the label rather than the grouping). Answering
    # this roster's question needs no outside knowledge for once: both halves are already
    # here as invented, and the label follows the `Example <Word>` convention this roster
    # itself asks for, so it names no product by construction.
    #
    # It replaced a bare `Tools:`, and the distinction is about WHERE a string sits, not
    # about the word: a FIXTURE VALUE arrives here as a declared skill, where the question
    # is whether it names a real product someone works with, so an ordinary noun still
    # costs a ruling. The same commit ships `Tools:` in `cv/compose.py`'s shape rule, as an
    # example of a FORBIDDEN line shape rather than a claimed skill, so THIS roster does not
    # rule on it (tests/test_prompt_neutrality.py is the ratchet that reaches shipped prompt
    # text, and it does). Do not read this entry as a finding that `Tools` is unsafe.
    "Example Category: Example Query",
    # Two already-rostered values joined by a SLASH, for the row 2 fixture proving the
    # slash-joined shape the rule names has a refused instance too. The join character is
    # the whole point of the value; both halves are already here as invented, so this
    # ratchet's question needs no outside knowledge for this one either.
    "Example Framework / Example Query",
})

# `_REVIEWED_FIXTURE_IDENTITIES` is about LEAD identities -- employers a fixture names.
# `Example Sans` is a font FAMILY in a @font-face fixture, beside genuine faces like
# "DejaVu Sans"; it names no firm, and rostering it would make the roster mean something
# wider than it says. Exempted by name, not by pattern, so a real employer that happened
# to end in "Sans" would still force the human call. Same shape as CLAUDE.md's cairo/pango
# carve-out from the place-name sweep. (Owner's ruling, 2026-08-24.)
#
# Every OTHER exemption here is DERIVED from `_REVIEWED_SKILL_VALUES` above, through the
# SAME `_CV_IDENTITY_RE` this sweep matches with -- not hand-listed beside it. A hand-typed
# second copy is a drift surface twice over: it can fall out of sync with the roster it is
# supposed to mirror, and `_CV_IDENTITY_RE`'s own letters-only capture means the STRING that
# needs listing (`Example Widget`, from `Example Widget3`) is not even the one a human
# typed -- exactly the kind of entry a later reader deletes as junk on sight. Skill values
# are reviewed on `_REVIEWED_SKILL_VALUES`, not here; this only re-derives what that roster
# already decided, at the granularity this OTHER sweep happens to need.
_CV_IDENTITY_EXEMPT = frozenset({"Example Sans"}) | {
    m.group(0) for v in _REVIEWED_SKILL_VALUES if (m := _CV_IDENTITY_RE.match(v))
}


def _cv_fixture_identities():
    found = set()
    for name in _CV_TEST_MODULES:
        path = _TESTS_DIR / name
        if not path.exists():
            continue
        found |= set(_CV_IDENTITY_RE.findall(path.read_text(encoding="utf-8")))
    return found


def test_the_cv_module_set_is_derived_and_not_hand_listed():
    """A hand-list is only safe while somebody remembers it, and twice now nobody did:
    `test_slop_phrase_retirement.py` at #181, and `test_cv_bundle.py` at #174 -- the very
    module the second one put new fixtures in. Deriving the set closes the class.

    Asserts the SCOPE, not the result: a glob that matches nothing satisfies every
    assertion over it, and for a negative guard like this one finding nothing IS the
    success case, so the count is the only thing that can catch a broken sweep.

    Pinned as TWO floors, not one. A single floor over the TOTAL (glob matches plus the
    three hand-listed exceptions) is satisfied even if the glob silently lost three
    modules, as long as the total still happened to clear 13 -- which it does today by
    coincidence (13 glob matches + 3 exceptions = 16, and a glob narrowed to exactly 10
    would still pass a bare `>= 13` total check). Pinning the glob's OWN count separately
    closes that: a narrowed glob now reds on its own floor before the total is even
    checked, independent of how many hand-listed exceptions happen to make up the rest.
    """
    glob_matched = {p.name for p in _TESTS_DIR.glob("test_cv_*.py")}
    assert len(glob_matched) >= 13, sorted(glob_matched)
    assert len(_CV_TEST_MODULES) >= len(glob_matched) + len(_CV_MODULES_NOT_MATCHING_THE_CONVENTION)
    assert "test_cv_bundle.py" in _CV_TEST_MODULES
    for extra in _CV_MODULES_NOT_MATCHING_THE_CONVENTION:
        assert extra in _CV_TEST_MODULES, extra


def test_the_cv_identity_collector_actually_finds_fixtures():
    # Same vacuity guard every other collector here carries: a regex that silently stops
    # matching makes the check below green over an empty set.
    found = _cv_fixture_identities()
    assert len(found) >= 3, (
        f"the CV identity sweep found {len(found)} names; it has stopped matching and the "
        "roster check below is now vacuous")


def test_cv_fixture_identities_are_on_the_reviewed_roster():
    """An employer written into CV BODY prose must still force the roster call.

    Measured before this existed: `Example Leverage` and `Example Scrum` were live CV
    fixture identities that NO collector in this file saw, so adding them needed no human
    to look. That is exactly what the roster exists to prevent, just at a position the
    four positional collectors do not reach.
    """
    unreviewed = sorted(_cv_fixture_identities()
                        - _REVIEWED_FIXTURE_IDENTITIES - _CV_IDENTITY_EXEMPT)
    assert unreviewed == [], (
        "these CV-fixture identities are not on _REVIEWED_FIXTURE_IDENTITIES: "
        f"{unreviewed}. Confirm each names no real firm, then add it to the roster.")


# ── Skill values (#168) ──────────────────────────────────────────────────────────────
#
# `_REVIEWED_SKILL_VALUES` itself is defined earlier, beside `_CV_IDENTITY_EXEMPT` -- that
# set derives from this roster, so the roster has to exist first. What follows is the two
# collectors and the tests that keep the roster honest against `tests/`.
#
# The same parameterised pattern the evidence `Company:` collector uses, so every shape
# measured for that key -- bare, quoted, dict/kwarg, and the escaped-`\n` block where a
# whole frontmatter section is ONE Python string literal -- is covered here for free.
# Deliberately NOT a member of `_IDENTITY_COLLECTORS`: that tuple feeds the EMPLOYER
# roster and carries its own `len(...) == 5` scope pin.
_SKILL_COLLECTOR = ("evidence Skills: (frontmatter or dict literal)",
                    _evidence_field_re("Skills"))


_SKILL_BLOCK_LIST_COLLECTOR = ("evidence Skills: (YAML block list)",
                               _evidence_block_list_re("Skills"))


# --- The AST collector: the four shapes no REGEX here can reach ----------------------
#
# Measured, and this is the FOURTH collector-evasion instance on this branch: after the
# final review round, `_all_fixture_skill_values()` collected 8 values while EVERY
# skill-shaped value that round added was invisible to it. The regex collectors above are
# anchored on a COLON, so they see a frontmatter line and a dict LITERAL and nothing else
# -- `dict(Skills="X")` has an `=`, a `@pytest.mark.parametrize` list has neither, and a
# CV fixture's own emitted `SKILLS` section is prose inside a string. The roster read
# green throughout, so "a new value forces a human call" never fired.
#
# (That is also why `_SKILL_COLLECTOR`'s label no longer says "dict/kwarg": it never
# matched a kwarg. Measured: `_evidence_field_re("Skills")` returns [] for
# `dict(Skills="Example Query")` and for `skills="Example Query"`, and matches only the
# dict-LITERAL spelling `{"Skills": "Example Query"}`. The label and the code now agree.)
#
# AST rather than a fourth regex, because all four shapes are about Python STRUCTURE
# (which argument, which decorator, which list, which name a value was bound to) rather
# than about text next to a colon -- the thing a regex is good at and has already been
# corrected twice here for. The fourth shape (constant indirection -- a `NAME = "literal"`
# binding threaded through a covered kwarg) was added by a #213 review round after this
# collector, which by then claimed to see "every shape", was planted end to end and stayed
# green over exactly that shape. See `_ast_skill_values` and `_string_const_bindings`.
_SKILL_BULLET_MARKERS = ("-", "•", "*", "–", "—")
_SKILL_KEY_PREFIX_RE = re.compile(r"""^\s*["']?Skills["']?\s*:\s*""", re.I)


def _is_skill_kwarg(name: str) -> bool:
    """Is `name` an argument that carries a `Skills:` VALUE?

    `Skills` itself (the `dict(Skills=...)` spelling this suite adopted precisely to dodge
    the colon-keyed collectors -- see `tests/test_cv_bundle.py`'s own docstring), plus the
    `_skills`-suffixed parameters the containment fixtures thread it through
    (`al_skills=`, `be_skills=`, `skills=`).

    Suffix-anchored, NOT substring: `skills_requested=` and `skills_unreadable=` are
    booleans about a FEATURE, not skill values, and a `"skills" in name` rule would sweep
    them in and then demand they be rostered.
    """
    n = name.lower()
    return n == "skills" or n.endswith("_skills")


def _skill_strings(node, consts=None) -> set:
    """The skill values in one AST value node: a string constant, or a
    list/tuple/set of them.

    Split on commas for the same reason `_all_fixture_skill_values` splits the regex
    collector's output: a `Skills:` value holds a comma-separated LIST, so
    `dict(Skills="Example Query, Example Framework")` is two identities and rostering the
    joined string would let a real product name ride in beside a reviewed one.

    `consts`, when given, resolves a bare NAME reference against `_string_const_bindings`'
    map of every simple `NAME = "literal"` assignment in the module -- module-level or a
    function-local, both bindings this collector was blind to until #213's review planted
    `dict(Skills=_SOME_CONST)` and `dict(Skills=some_local)` end to end and the roster swept
    clean over both. Still not general dataflow: a name that is reassigned, built from an
    f-string, read from a file, or returned by a function call resolves to nothing here,
    the same way it resolves to nothing for `_string_const_bindings` itself.
    """
    out = set()
    nodes = (node.elts if isinstance(node, (ast.List, ast.Tuple, ast.Set)) else [node])
    for n in nodes:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            raws = {n.value}
        elif consts is not None and isinstance(n, ast.Name) and n.id in consts:
            raws = consts[n.id]
        else:
            continue
        for raw in raws:
            # One fixture threads a whole frontmatter LINE through the kwarg
            # (`_FM.format(skills="Skills: Example Query, Example Framework\n")`), so the
            # key itself has to come off or the roster grows a `Skills:` entry that names
            # nothing. The VALUES there are already reached by the frontmatter regex
            # collector; stripping here just stops this one double-counting the key.
            out |= {p.strip() for p in _SKILL_KEY_PREFIX_RE.sub("", raw).split(",")
                    if p.strip()}
    return out


def _string_const_bindings(tree) -> dict:
    """Every simple `NAME = "literal"` assignment in one module -- module-level or local to
    a function -- mapped to the SET of string values ever bound to that name.

    This is the fourth shape `_ast_skill_values` reaches, closing the gap #213's review
    measured live: a skill value handed to a covered kwarg through a named constant
    (`_SKILL = "Example X"` ... `dict(Skills=_SKILL)`) rather than inline. Deliberately NOT
    general dataflow -- only a direct `NAME = "<string literal>"` assignment is resolved.
    A name built from an f-string, a function call, string concatenation, or read from a
    file is invisible here, on purpose: chasing those would turn a small AST sweep into a
    partial interpreter, and the failure mode of NOT chasing them is a value that still
    reads as a plain string literal at its assignment site, so a human skimming a diff has
    something to catch even where this collector cannot.

    Walking the WHOLE tree rather than scoping to one function's own body is deliberate
    too: a name is matched purely by identifier, so this can over-collect (the same name
    bound to two different literals in two different functions contributes both), and
    over-collection is the safe direction for a ratchet whose failure mode is a value it
    never sees at all -- see `_ast_skill_values`'s own docstring.
    """
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out.setdefault(node.targets[0].id, set()).add(node.value.value)
    return out


def _skills_run(lines) -> set:
    """The bullet values of every emitted `SKILLS` section in `lines`.

    Mirrors `cv/validate.py`'s `section_spans` rather than inventing a second rule: the
    run starts at a bare `SKILLS` line in ANY case and continues across blanks AND across
    any heading the format contract does not define -- a group heading (`Languages`) and
    an off-contract section header (`PUBLICATIONS`) alike, in either case -- ending only
    at `WORK EXPERIENCE`/`CERTIFICATES`/`EDUCATION`. Stopping at the first non-bullet instead
    would miss exactly the value that motivated this collector -- `_GROUPED_TAIL_CV`'s
    second bullet sits UNDER a `Languages` heading, and a collector that stopped there
    would sweep clean over it while the gate checks it.

    `in_work` plays no part beyond ending the run: that arm of `section_spans` is about
    which CHECK claims a line, and this is a roster asking a different question -- would a
    reader see this under SKILLS.

    The terminator set is DERIVED from `tests/template_content.py`'s
    `composer_headings()`, the same closed set `section_spans` is pinned against, rather
    than hand-listed here -- a pair typed by hand went stale the moment the gate's rule
    stopped keying on ALL-CAPS, and the collector then swept WORK bullets into the skill
    roster (measured: four of them). One deliberate divergence: `PROFILE` clears `in_work`
    here and does not in `section_spans`, which can only keep a LATER run alive longer, so
    it over-collects rather than under-collects -- the safe direction for a ratchet whose
    failure mode is a value it never sees at all.

    EVERY heading compare here is case-INSENSITIVE, opener included, exactly as
    `section_spans` compares `line.strip().upper()`. That matters at the opener above all:
    a case-SENSITIVE opener meant a fixture spelling its header `Skills` opened a run in
    the GATE and not in this sweep, so an unreviewed value could reach the tree with the
    ratchet green -- the same class of blind spot as the four WORK bullets above, one step
    earlier. What stops the folded opener from firing on unrelated text is not a narrower
    compare but a narrower INPUT: see `_ast_skill_values`' two call sites, which hand this
    function only blocks that are actually newline-joined DOCUMENTS.
    """
    from tests.template_content import composer_headings

    contract = composer_headings()
    out, in_skills, in_work = set(), False, False
    for line in lines:
        stripped = line.strip()
        # Uppercased, as `section_spans` compares (`line.strip().upper()`), for the
        # opener and both terminator arms alike -- the two engines have to agree on what
        # a heading IS or each divergence is a hole. Measured, one in each direction: a
        # case-sensitive TERMINATOR read `  Education  ` as ordinary content and kept a
        # run alive the gate had already ended, sweeping four WORK bullets out of
        # `tests/test_cv_validate.py`'s random-document alphabet into the skill roster;
        # a case-sensitive OPENER missed a `Skills`-cased header the gate opens on, so an
        # unreviewed value could ship with this sweep green.
        #
        # Folding the opener is only safe because `_ast_skill_values` hands this function
        # DOCUMENTS and not arbitrary string lists -- see its two call sites. Against every
        # list literal, a folded opener fired on 20 argv lists (`main(["skills", "add",
        # "--name", ...])`), whose `--flag` elements strip to nine field names; rostering
        # `name`/`id`/`verified` as reviewed skill values would pre-approve those strings
        # for good, which is worse than the noise.
        heading = stripped.upper()
        if heading == "SKILLS":
            in_skills = True
            continue
        if heading == "WORK EXPERIENCE":
            in_work, in_skills = True, False
            continue
        if heading in contract:
            in_work, in_skills = False, False
            continue
        if not in_skills:
            continue
        if stripped.startswith(_SKILL_BULLET_MARKERS):
            item = stripped.lstrip("-•*–— ").strip().strip('"').strip("'")
            if item:
                out |= {p.strip() for p in item.split(",") if p.strip()}
        elif stripped and in_work:
            in_skills = False
    return out


def _newline_joined_blocks(tree) -> set:
    """`id()` of every list/tuple literal in `tree` that is joined into a DOCUMENT --
    a direct argument of a `"\\n".join(...)` call, on either side of the `+` in the
    `["SKILLS"] + [f"- {i}" for i in items]` builder shape.

    This is what makes `_skills_run`'s case-insensitive opener safe, and it is a
    different kind of narrowing from the one it replaced. A narrower COMPARE (an exact
    `SKILLS`) gave the sweep a blind spot the gate does not have: real CV text spelled
    `Skills` reached `validate()` and not the roster. A narrower INPUT excludes text that
    is not a CV at all -- an argv list never reaches `validate()` by any path, so nothing
    the gate checks can hide behind this.

    Measured over `tests/` rather than argued: with the opener folded and no input rule,
    the list branch collected from 20 `main(["skills", "add", "--name", ...])` argv lists,
    whose `--flag` elements strip to nine field names (`name`, `id`, `verified`, ...).
    This rule drops all nine and keeps every genuine value the branch ever produced
    (`Example Query`, `Totally Invented Skill`, both from `_GROUPED_TAIL_CV`).

    THE RESIDUAL, stated because a rule that names no cost reads as free: a fixture that
    builds CV lines into a VARIABLE and joins it somewhere else is not seen here. No
    fixture in `tests/` does that today -- the same measurement above is what says so, not
    an assumption -- and the shape it would need is unusual, since a CV fixture written as
    a list exists precisely to be joined on the spot. Deliberately NOT closed by also
    accepting a list that carries a second contract heading: every genuine list here
    already satisfies both rules, so that clause would be unreachable, and an unreachable
    guard deletes green -- the equivalent-mutant shape this repo refuses everywhere else.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "join"):
            continue
        sep = f.value
        if not (isinstance(sep, ast.Constant) and isinstance(sep.value, str)):
            continue
        # A REAL newline or the two-character escape: a packed fixture joins its lines
        # with `"\\n"`, the same shape `_block_list_items` already normalises for.
        if "\n" not in sep.value and "\\n" not in sep.value:
            continue
        for arg in node.args:
            sides = (arg.left, arg.right) if isinstance(arg, ast.BinOp) else (arg,)
            for side in sides:
                if isinstance(side, (ast.List, ast.Tuple)):
                    out.add(id(side))
    return out


def _called_name(node) -> str:
    """The dotted tail of a call's callee, or "" -- `_cv_with_skills` for both a bare
    call and an `X._cv_with_skills` attribute access."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _skills_section_builders(tree) -> set:
    """Names of functions in ONE module that emit a bare `SKILLS` section header.

    `_cv_with_skills(items)` builds `"SKILLS"` plus one bullet per item, so its CALLER's
    literal list is a list of skill values -- but the header lives in the callee, where no
    scan of the call site can see it. Resolving the helper by what it CONTAINS is what
    makes that shape reachable without keying on a name (see the call rule's own comment
    for the false positive a name rule was measured to produce).
    """
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(isinstance(n, ast.Constant) and n.value == "SKILLS"
               for n in ast.walk(fn)):
            out.add(fn.name)
    return out


def _parametrize_values(fn) -> dict:
    """`{argname: {literal string values}}` for one function's `parametrize` decorators.

    Single-argname form only (`parametrize("value", [...])`), which is every shape this
    suite uses for a skill fixture. A comma-joined argname list ("a,b") is deliberately
    skipped rather than guessed at: a wrong pairing would roster a value under the wrong
    parameter, and a MISSED one shows up as an unrostered value the moment it is a skill,
    which is the direction this ratchet is meant to fail in.
    """
    out = {}
    for dec in fn.decorator_list:
        if not (isinstance(dec, ast.Call) and _called_name(dec) == "parametrize"
                and len(dec.args) >= 2):
            continue
        name = dec.args[0]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)
                and "," not in name.value):
            continue
        out.setdefault(name.value.strip(), set()).update(_skill_strings(dec.args[1]))
    return out


def _ast_skill_values(text) -> set:
    """Every skill value one test module declares in a shape no regex here can reach.

    FOUR shapes. The first three are what this branch introduced originally; the fourth
    (constant indirection) closed a gap #213's review found in THOSE three: each is only
    reached when the value sits inline at the call site, and none of them followed a name
    to where it was actually bound.

      kwarg          `dict(Skills="Example Query")`, `al_skills="Examplestore3"`
      parametrize    `@parametrize("value", [".NET"])` feeding `dict(Skills=value)`
      emitted SKILLS a CV fixture's own `SKILLS` section, whether written as one
                     triple-quoted string or as a list of per-line string constants
      const indirect `_SKILL = "Example X"` (module-level) or `skill = "Example X"`
                     (function-local), later passed as `dict(Skills=_SKILL)` /
                     `dict(Skills=skill)` -- see `_string_const_bindings`

    What STILL cannot be seen, stated honestly because a label claiming coverage the code
    lacks is why nobody looks (this file's own recurring lesson, now a fourth time): a
    COMPUTED value (string concatenation, `.format`, a comprehension), an f-string, a value
    read from a file or environment, or one returned by a function call. Each of those
    would need actual dataflow analysis, not an AST sweep, and none is attempted here.

    A SyntaxError returns the empty set rather than raising: this sweeps every file under
    `tests/`, and one unparseable file must not take the whole guard down -- but it must
    not be silent either, so `test_every_test_module_parses_for_the_ast_collector` fails
    the build on one instead. Swallowing it HERE and asserting on it THERE is what keeps a
    parse failure from reading as "no skill values found", the empty-sweep shape this
    file's own docstring warns about.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    consts = _string_const_bindings(tree)
    values = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = _parametrize_values(fn)
        if not params:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg and _is_skill_kwarg(kw.arg)
                            and isinstance(kw.value, ast.Name)):
                        values |= params.get(kw.value.id, set())
            elif isinstance(node, ast.Dict):
                for k, val in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "Skills"
                            and isinstance(val, ast.Name)):
                        values |= params.get(val.id, set())
    builders = _skills_section_builders(tree)
    joined_blocks = _newline_joined_blocks(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and _is_skill_kwarg(kw.arg):
                    values |= _skill_strings(kw.value, consts)
            # A helper in THIS module that builds a SKILLS section, handed a literal
            # sequence: `_cv_with_skills(["Example Query", "Kubernetes"])`. The header
            # those items end up under is inside the helper, so no run-scan can see this
            # shape from the call site.
            #
            # Resolved by what the helper CONTAINS (a bare "SKILLS" string constant),
            # never by its NAME. A name rule was tried first and measured to sweep
            # `classify_negatives_vs_skills(["never claim anything"], ...)` -- whose first
            # argument is a list of NEGATIVE CONSTRAINTS, the opposite of a skill -- onto
            # the roster. Containment is also the property that matters: a helper that
            # never emits the header cannot put its argument under one.
            if _called_name(node) in builders:
                for arg in node.args:
                    if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
                        values |= _skill_strings(arg, consts)
        elif isinstance(node, ast.Dict):
            # A dict KEY is matched case-SENSITIVELY against the frontmatter key
            # (`{"Skills": "Example Widget3"}`), the same way `_evidence_field_re` matches
            # it. Lowercase `"skills"` is the evidence KIND id, not a value-carrying
            # field: `{"experience": "8801", "skills": "8802"}` in
            # tests/test_evidence_store.py maps kinds to sentinels, and a
            # case-insensitive key rule was measured to roster `8802` off it. A KWARG is
            # matched loosely by contrast (`_is_skill_kwarg`), because there the name is a
            # Python parameter -- `al_skills=`, `be_skills=` -- and not a frontmatter key.
            for k, val in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "Skills"):
                    values |= _skill_strings(val, consts)
        elif isinstance(node, (ast.List, ast.Tuple)):
            # A CV written as one string per LINE -- `"\n".join([...])`, the shape
            # `_GROUPED_TAIL_CV` uses. Restricted to lists that are ACTUALLY joined into
            # a document (`_newline_joined_blocks`, which has the measurement): the
            # branch used to walk every list literal, which was harmless only while
            # `_skills_run`'s opener was case-sensitive and is what that opener was
            # wrongly narrowed to compensate for. This narrows the INPUT to what this
            # comment already claimed the branch was for, rather than narrowing what the
            # collector understands about CV text.
            lines = [e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if lines and id(node) in joined_blocks:
                values |= _skills_run(lines)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A CV written as ONE string -- the `_CV` triple-quoted shape. `\\n` is
            # normalised first for the same reason `_block_list_items` does it: a packed
            # fixture joins its lines with the two-character escape, not a real newline.
            #
            # The cheap prefilter is case-INSENSITIVE, matching the opener it stands in
            # front of: keyed on the literal `SKILLS` it silently vetoed every
            # `Skills`-cased CV before `_skills_run` was ever reached, so folding the
            # opener alone would have left the same hole one level up. MULTI-LINE is the
            # input rule on this branch, and it costs nothing real -- a one-line string
            # cannot hold a heading AND a bullet beneath it, so it can only be a bare
            # `"skills"`-ish literal, never a document.
            lines = node.value.replace("\\n", "\n").splitlines()
            if len(lines) > 1 and "SKILLS" in node.value.upper():
                values |= _skills_run(lines)
    return values


def _all_fixture_skill_values():
    """Every individual skill value any fixture declares, across BOTH spellings.

    SPLIT ON COMMAS: `Skills:` holds a comma-separated list, so one match is `Example Query,
    Example Framework` -- two identities, not one. Rostering the joined string would let a
    real product name ride into `tests/` inside a pair whose other half was reviewed.

    The YAML block-list spelling (`_evidence_block_list_re`, above) is a SECOND collector,
    not an alternative branch of the first: `_SKILL_COLLECTOR`'s alternation requires a
    value token immediately after `Skills:`, so it cannot also match a block list where the
    value starts on the next line. A skill named only in that shape would otherwise be
    invisible to this whole roster.
    """
    values = set()
    for raw in _collect(_SKILL_COLLECTOR[1]):
        values |= {part.strip() for part in raw.split(",") if part.strip()}
    for text in _test_sources():
        values |= set(_block_list_items(_SKILL_BLOCK_LIST_COLLECTOR[1], text))
        # The THIRD collector, and the one that is not a regex at all: the kwarg,
        # parametrize and emitted-SKILLS shapes, which are about Python structure rather
        # than about text beside a colon. See `_ast_skill_values` for why all three were
        # invisible to the two above, and to what that cost.
        values |= _ast_skill_values(text)
    return values


def test_the_evidence_skills_collector_sees_every_shape_it_claims_to():
    """Companion to `test_the_evidence_company_collector_sees_every_shape_it_claims_to`,
    covering the two shapes that collector never had to: a comma-joined value must split
    into SEPARATE identities, and the YAML block-list spelling `_evidence_field_re`
    cannot see at all (see `_evidence_block_list_re`'s docstring for why).

    Every string below is written as the collectors see it -- fragments of Python SOURCE.
    `\\n` inside them is two characters, backslash then `n`, exactly as a real
    packed-frontmatter fixture writes it (one Python string literal joining several
    frontmatter lines). This file is `_SELF`, excluded from `_test_sources()`, so none of
    these values reaches `_REVIEWED_SKILL_VALUES`.
    """
    comma_pattern = _SKILL_COLLECTOR[1]
    block_pattern = _SKILL_BLOCK_LIST_COLLECTOR[1]

    # Comma spelling: ONE regex match, split into TWO identities -- not the joined string
    # `_REVIEWED_SKILL_VALUES` would otherwise need to carry as a single entry.
    comma_text = 'Skills: Example Query, Example Framework\\nverified: 2026-01-01'
    [comma_raw] = comma_pattern.findall(comma_text)
    comma_found = [part.strip() for part in comma_raw.split(",")]
    assert comma_found == ["Example Query", "Example Framework"], comma_found

    # Block-list spelling, joined with the literal two-character `\n` escape -- the shape
    # every packed-frontmatter fixture in this repo actually uses.
    escaped_block = (
        'Skills:\\n  - Example Query\\n  - Example Framework\\nverified: 2026-01-01')
    escaped_items = _block_list_items(block_pattern, escaped_block)
    assert escaped_items == ["Example Query", "Example Framework"], escaped_items

    # The SAME block-list spelling joined with a REAL newline (a triple-quoted fixture
    # rather than one packed literal) must count identically -- neither boundary form may
    # be the only one recognised.
    real_block = "Skills:\n  - Example Query\n  - Example Framework\nverified: 2026-01-01"
    real_items = _block_list_items(block_pattern, real_block)
    assert real_items == ["Example Query", "Example Framework"], real_items

    # A block list under a DIFFERENT key must not leak into the Skills sweep -- this is
    # the live `Category:` shape (`tests/test_core_vault_cv.py`), and a collector keyed
    # loosely enough to match it too would attribute someone else's field value to Skills.
    other_key_block = "Category:\n  - Process\n  - Leadership\nverified: x"
    assert _block_list_items(block_pattern, other_key_block) == []

    # A bare `Skills:` with an INLINE value (the comma spelling) has no block-list run to
    # find -- the two collectors must not double-count the same fixture.
    inline_text = 'Skills: Example Query, Example Framework\\nverified: x'
    assert _block_list_items(block_pattern, inline_text) == []

    # REGRESSION (found by this task's own planting witness, which used a different value
    # in this same shape -- "Example Torrent", planted in tests/test_doctor.py and
    # reverted): a bare `Skills:` followed by a REAL newline then a block-list item must
    # not ALSO satisfy the comma collector's bare-value alternative. `\s` matches a real
    # newline, so `Skills:\s*` used to swallow the line break and start its value capture
    # on `- Example Query` itself -- a bogus DASH-PREFIXED second identity for the same
    # value the block-list collector already found correctly. `_evidence_field_re`'s
    # post-colon gap is `[ \t]*` specifically to close this; a regression here reopens it
    # silently, for `Company:` too, since the two share one pattern.
    assert comma_pattern.findall(real_block) == []


def test_evidence_skill_values_are_on_the_reviewed_roster():
    """#168 put a candidate's real skills into a new `Skills:` frontmatter position, and
    measured at the time it was added, NO collector in this file reached it -- the evidence
    collector is keyed on the literal `Company`. A technology name could ship into `tests/`
    with every guard in this file green.

    A ratchet, not a classifier, exactly like the employer roster above: nothing here can
    tell an invented name from a real product, so a new value fails the build until a human
    records the call in `_REVIEWED_SKILL_VALUES`.
    """
    found = _all_fixture_skill_values()
    # SCOPE first. For a negative guard an empty sweep reads exactly like a clean one, and
    # `unreviewed` below would be empty for a collector that matched nothing at all.
    assert found, (
        "the Skills: collector matched no fixture value anywhere in tests/ -- either the "
        "corpus lost its skills fixtures or the pattern stopped matching; both make every "
        "assertion below vacuous")

    unreviewed = sorted(found - _REVIEWED_SKILL_VALUES)
    assert not unreviewed, (
        f"unreviewed skill value(s) in tests/: {unreviewed}. Nothing local can tell whether "
        f"a technology-shaped name is invented or names a real product, so this needs a "
        f"human call: confirm it is synthetic and add it to _REVIEWED_SKILL_VALUES. Prefer "
        f"`Example <Word>`.")


def test_the_skill_roster_has_no_stale_entries():
    """The roster is a record of calls a human made about values that EXIST. An entry whose
    value has left the corpus is a call about nothing, and it silently widens what the next
    reviewer sees as already-approved. Same shape as the employer roster's own staleness
    check.
    """
    stale = sorted(_REVIEWED_SKILL_VALUES - _all_fixture_skill_values())
    assert not stale, (
        f"_REVIEWED_SKILL_VALUES lists {stale}, which no fixture declares any more -- drop "
        f"the entr{'y' if len(stale) == 1 else 'ies'} rather than leaving a reviewed value "
        f"that pre-approves a future re-introduction nobody looked at")


def test_every_test_module_parses_for_the_ast_collector():
    """The SCOPE guard for `_ast_skill_values`' swallowed SyntaxError.

    That function returns the empty set on an unparseable module so one bad file cannot
    take the whole sweep down -- but an empty set is indistinguishable from "this file
    declares no skills", which is the fail-open shape this file exists to close. Asserting
    on parseability HERE is what keeps the swallow honest: a file that stops parsing
    reddens by name instead of quietly contributing nothing.

    It also pins the SCOPE of the sweep itself. `_test_sources()` returning [] would make
    every collector assertion vacuous, and a count floor catches that.
    """
    sources = _test_sources()
    assert len(sources) > 20, (
        f"_test_sources() found only {len(sources)} modules -- the sweep's own corpus is "
        "missing, which makes every roster assertion below vacuous")
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == _SELF:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:            # pragma: no cover - a red build, by design
            raise AssertionError(
                f"{path.name} does not parse ({e}), so _ast_skill_values() swallows it "
                "and silently contributes no skill values to the roster sweep") from e


def test_the_ast_skill_collector_sees_every_shape_it_claims_to():
    """The four shapes no regex in this file can reach, each measured as a fragment of
    Python SOURCE -- and each one a shape that WAS live in `tests/` while the roster read
    green.

    Written against `_ast_skill_values` (which parses) rather than against a pattern,
    because that is the unit: the claim is about which AST positions are read, not about
    which characters match. This file is `_SELF`, excluded from `_test_sources()`, so
    nothing here reaches `_REVIEWED_SKILL_VALUES`.
    """
    # (1) KWARG. Both the `dict(Skills=...)` spelling this suite adopted specifically to
    # dodge the colon collectors, and the `_skills`-suffixed parameters that thread a
    # value through a fixture builder.
    assert _ast_skill_values(
        'f(fields=dict(Skills="Example Alpha, Example Beta"))') == {
            "Example Alpha", "Example Beta"}
    assert _ast_skill_values('_two(al_skills="Example Gamma", be_skills="")') == {
        "Example Gamma"}

    # A kwarg carrying a whole frontmatter LINE (a live shape in
    # tests/test_evidence_kinds.py) must yield the VALUES, never the key.
    assert _ast_skill_values(
        '_FM.format(skills="Skills: Example Delta\\n")') == {"Example Delta"}

    # ...and a boolean FEATURE flag whose name merely contains "skills" is not a value.
    assert _ast_skill_values("compose(skills_requested=True)") == set()

    # (2) PARAMETRIZE feeding a skill kwarg. The list is in a DECORATOR, syntactically
    # nowhere near the word Skills; only the dataflow to the kwarg connects them.
    src = (
        '@pytest.mark.parametrize("value", ["Example Epsilon", "Example Zeta"])\n'
        'def test_x(value):\n'
        '    build(fields=dict(Skills=value))\n')
    assert _ast_skill_values(src) == {"Example Epsilon", "Example Zeta"}
    # The same list with NO skill kwarg behind it stays out -- the dataflow is the claim,
    # not the mere presence of a parametrize list.
    assert _ast_skill_values(
        '@pytest.mark.parametrize("value", ["Example Eta"])\n'
        'def test_x(value):\n'
        '    build(title=value)\n') == set()

    # (3) EMITTED SKILLS SECTION, in the two shapes CV fixtures here actually use: one
    # triple-quoted string, and one string per line in a list.
    assert _ast_skill_values('CV = """SKILLS\n- Example Theta\n- Example Iota\n"""') == {
        "Example Theta", "Example Iota"}
    assert _ast_skill_values('CV = "\\n".join(["SKILLS", "- Example Kappa"])') == {
        "Example Kappa"}

    # A GROUP HEADING does not end the run, matching `section_spans` -- the bullet under
    # `Languages` is checked by the gate, so the roster must see it too. This is the exact
    # value that motivated the whole collector.
    assert _ast_skill_values(
        'CV = "\\n".join(["SKILLS", "- Example Lambda", "Languages", "- Example Mu"])'
    ) == {"Example Lambda", "Example Mu"}

    # Nor does an OFF-CONTRACT section header, however loudly it is spelled: since the
    # terminator became the contract's own heading set, `section_spans` reads past
    # `PUBLICATIONS` and containment-checks the bullet under it, so the roster must see
    # that value too. Keying this collector on ALL-CAPS instead would sweep clean over a
    # skill fixture the gate does check.
    assert _ast_skill_values(
        'CV = "\\n".join(["SKILLS", "- Example Nu", "PUBLICATIONS", "- Example Rho"])'
    ) == {"Example Nu", "Example Rho"}

    # A CONTRACT heading DOES end it, so a following section's bullets are not rostered
    # as skills. Spelled in MIXED case on purpose: `section_spans` compares
    # `line.strip().upper()`, so `Education` ends the gate's run, and a collector that
    # only recognised the shouted spelling would keep collecting past it.
    assert _ast_skill_values(
        'CV = "\\n".join(["SKILLS", "- Example Sigma", "Education", "- a degree"])'
    ) == {"Example Sigma"}

    # THE OPENER IS CASE-INSENSITIVE TOO, in both fixture spellings. This is the row that
    # reds if it is ever narrowed back to an exact `SKILLS`: the gate opens its run on
    # `Skills` (it compares `line.strip().upper()`), so a fixture header spelled that way
    # is checked by `validate()` and must be seen here, or an unreviewed value ships with
    # this sweep green.
    assert _ast_skill_values(
        'CV = "\\n".join(["Skills", "- Example Tau"])') == {"Example Tau"}
    assert _ast_skill_values('CV = """Skills\n- Example Upsilon\n"""') == {
        "Example Upsilon"}

    # ...and what keeps that fold from firing on text that is not a CV is the INPUT rule,
    # not a narrower compare. An argv list is a multi-element list of strings whose
    # `--flag` members strip to plausible-looking words, and 20 of them live in
    # `tests/test_evidence_cli.py`; none is joined into a document, so none opens a run.
    # A one-line string cannot hold a heading and a bullet beneath it either.
    assert _ast_skill_values(
        'main(["skills", "add", "--name", "x", "--proficiency", "expert"])') == set()
    assert _ast_skill_values('KIND = "skills"') == set()
    # The same list, joined, IS a document and DOES collect -- so the rule above is an
    # input restriction and not a way of never seeing lists at all.
    assert _ast_skill_values(
        'CV = "\\n".join(["skills", "- Example Phi"])') == {"Example Phi"}

    # While WORK EXPERIENCE is live the run ends at a non-bullet line, exactly as
    # `section_spans` does -- without this, a role's own bullets would be rostered as
    # skills. Measured: `- An uncited claim` was collected before this arm existed.
    assert _ast_skill_values(
        'CV = "\\n".join(["WORK EXPERIENCE", "SKILLS", "- Example Xi", "Example Beta",'
        ' "- An uncited claim"])') == {"Example Xi"}

    # (3b) A BUILDER's caller: the header lives inside `_cv_with_skills`, so the call site
    # has no SKILLS line of its own to scan. Resolved by what the helper CONTAINS.
    builder = ('def _cv_with_skills(items):\n'
               '    return "\\n".join(["SKILLS"] + [f"- {i}" for i in items])\n')
    assert _ast_skill_values(builder + 'x = _cv_with_skills(["Example Omicron"])') == {
        "Example Omicron"}
    # A helper that never emits the header is NOT a builder, however its name reads --
    # this is the live `classify_negatives_vs_skills(<negatives>, <inventory>)` shape,
    # whose first argument is the OPPOSITE of a skill and was measured onto the roster by
    # an earlier name-keyed rule.
    assert _ast_skill_values(
        'classify_negatives_vs_skills(["never claim anything"], [])') == set()

    # (4) CONST INDIRECTION: a `NAME = "literal"` binding, module-level or function-local,
    # later threaded through the SAME covered kwarg the earlier cases only ever reach
    # inline. This is the shape #213's review planted end to end -- a value invisible to
    # shapes (1)-(3), which look only at the call site, never at where a name was bound.
    assert _ast_skill_values(
        '_SKILL = "Example Pi"\n'
        'build(fields=dict(Skills=_SKILL))\n') == {"Example Pi"}
    assert _ast_skill_values(
        'def test_x():\n'
        '    skill = "Example Rho"\n'
        '    build(fields=dict(Skills=skill))\n') == {"Example Rho"}
    # Still not general dataflow: a name never bound to a literal anywhere in the module
    # resolves to nothing, rather than raising or guessing.
    assert _ast_skill_values('build(fields=dict(Skills=_UNBOUND))') == set()
    # Nor is it type inference -- a name bound to a non-string literal is never coerced
    # into a skill value.
    assert _ast_skill_values(
        '_COUNT = 3\n'
        'build(fields=dict(Skills=_COUNT))\n') == set()

    # A lowercase `skills` is the evidence KIND id, not a value-carrying frontmatter key.
    # Measured: a case-insensitive dict-key rule rostered the sentinel `8802` off
    # tests/test_evidence_store.py's `{"experience": "8801", "skills": "8802"}`.
    assert _ast_skill_values('s = {"experience": "8801", "skills": "8802"}') == set()

    # An unparseable module contributes nothing rather than raising -- the swallow
    # `test_every_test_module_parses_for_the_ast_collector` above keeps honest.
    assert _ast_skill_values("def (:") == set()


# --- README's illustrative lead note (#221) -------------------------------------------------
#
# Every identity sweep above reads `_TESTS_DIR`, so README.md is outside ALL of them; the one
# neutrality guard that reaches it is test_no_leaked_files.py's absolute-home-path gate, which
# is repo-wide because its pathspec is empty. That cost nothing until #221 rewrote README with
# a full lead-identity block in it -- company, role, location, salary, url, role_type -- in the
# file `pyproject.toml` names as `readme`, so it is published to PyPI as well as GitHub.
#
# NARROW on purpose. A roster sweep over README PROSE is not the fix: it would fire on
# MrReasonable, Camofox, Homebrew, WeasyPrint, Obsidian and every other legitimate proper noun,
# and a guard that fires on good content is a guard that gets suppressed.
#
# SWEPT: the identities with a decidable rule -- company, location and every url -- read from
# the WHOLE block, both its frontmatter keys and the rendered restatements below the closing
# `---`. NOT `role`, `salary` or `role_type`: no roster exists for a job title or a day rate,
# and inventing one would be the classifier this file's own docstring argues against.
#
# Naming the KEYS rather than the identities is what went wrong twice. "the note's frontmatter
# keys" read to three reviewers as covering all six; correcting it to name three keys then
# described the code exactly while leaving the note's rendered half -- `# <company> - <role>`,
# `**Location:**`, `**URL:**` -- swept by nothing, so a real employer, place and ATS host all
# shipped GREEN past the guard written to stop precisely that. Scope this comment by IDENTITY,
# never by spelling, or the next spelling is unguarded again.
#
# The block is ISOLATED FIRST, and the sweep runs INSIDE it. Sweeping the whole file lets any
# other `company:` line anywhere in README satisfy the non-vacuity assertion while the sample
# note itself is reformatted or deleted -- measured on this guard's first cut, where a second
# note carrying an unreviewed employer and a real host shipped green.
#
# Values are read QUOTED OR BARE. The first cut matched `key: "value"` only, and README's own
# note already mixes bare scalars (`status: new`, `score: 0`), so a bare `company: Real Ltd`
# was invisible to the sweep while the scope assertion stayed satisfied by the quoted `url:`.
_README_PATH = _TESTS_DIR.parent / "README.md"
# Fence handling lives in `tests/markdown_fences.py`, shared with tests/test_docs_claims.py
# and implemented line by line against CommonMark 4.5. It was a regex duplicated across the two
# files until PR #222 round 6; CodeRabbit corrected those regexes on three consecutive rounds,
# one spec clause per round, and the round-4 attempt to stop the drift -- pinning the two
# patterns to each other -- did not survive round 5, because both were wrong in the same way.
# One implementation with a named branch per rule is what generalises; the agreement test that
# policed the duplication is gone with the duplication.
_README_FM_FIELD = re.compile(
    r"^(?P<key>company|location|url):[ \t]*(?P<val>.*?)[ \t]*$", re.M)

# The block does not stop at its frontmatter. Below the closing `---` the note RESTATES the
# same three identities in rendered form -- `# <company> - <role>`, a `**Location:**` line and
# a `**URL:**` line -- and none of those spellings is anchored `^key:`, so the frontmatter
# sweep alone cannot see them. Measured: a real employer in the heading, a real place in
# `**Location:**` and an ATS host in `**URL:**` all shipped GREEN, individually and together,
# while the identical substitutions in the frontmatter half all failed. Two reviewers found
# this independently. Sweep the WHOLE block: every url-shaped token through `_is_reserved`,
# and the rendered company/location restatements against the same rosters as their keys.
_RENDERED_URL = re.compile(r"(?<![\w@:])(?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>()\[\]`'\"]+)")
_RENDERED_HEADING = re.compile(r"^#[ \t]+(?P<company>.+?)[ \t]+-[ \t]+.+$", re.M)
_RENDERED_LOCATION = re.compile(r"^\*\*Location:\*\*[ \t]*(?P<location>[^|\n]+?)[ \t]*(?:\||$)", re.M)

# `Example City` is deliberately NOT added to `_REVIEWED_CORPUS_LOCATIONS`. That roster is for
# CAPTURED board values whose internal token structure has to survive `_norm_location` -- its
# own comment says to use the generated family for anything invented -- and README's note is an
# illustration written by hand, not a capture. A separate one-value set keeps the two scopes
# from being conflated by whoever extends either next.
_README_REVIEWED_LOCATIONS = frozenset({"Example City"})


def _readme_sample_note_fields() -> dict:
    """{"frontmatter": {key: [value, ...]}, "rendered": {...}} for README's sample lead note.

    The two halves are kept APART rather than merged. Merged, the non-vacuity assertion below
    is satisfied by whichever half still has the key -- measured, ALL SIX half-removals (each
    of company/location/url, from either half) left the guard green, so a frontmatter key
    renamed to `employer:` would carry a real employer with nothing sweeping it while the
    rendered heading kept the check happy. A guard whose scope assertion can be satisfied by
    the half that did not change is not asserting scope.
    """
    text = _README_PATH.read_text(encoding="utf-8")
    assert not unclosed_fence(text), (
        "README has an unclosed code fence. Refusing to select a sample note from it: an "
        "unclosed fence runs to the end of the document, so a second identity block placed "
        "after one is swallowed by it -- the count below would still read 1 and the new block "
        "would never be swept.")
    blocks = [b for b in fenced_blocks(text) if _README_FM_FIELD.search(b)]
    assert len(blocks) == 1, (
        f"expected exactly ONE fenced block in README carrying lead frontmatter, found "
        f"{len(blocks)}. Zero means the sample note was reformatted or removed and this sweep "
        f"is enumerating nothing; more than one means a second identity block shipped and this "
        f"guard needs to say which it covers.")

    lines = blocks[0].split("\n")
    marks = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    assert len(marks) >= 2, (
        "README's sample note has no closing `---`, so its frontmatter and rendered halves "
        "cannot be told apart and only one of them would be swept.")
    frontmatter = "\n".join(lines[marks[0] + 1:marks[1]])
    rendered = "\n".join(lines[marks[1] + 1:])

    fm = {}
    for m in _README_FM_FIELD.finditer(frontmatter):
        value = m.group("val")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]      # quoted or bare: both are valid YAML, both are swept
        fm.setdefault(m.group("key"), []).append(value)

    # The rendered half restates the same identities in different spellings, none of them
    # anchored `^key:`. A url is self-identifying, so it needs no per-spelling pattern and is
    # swept wherever it sits in this half -- `**URL:**` line or prose.
    rend = {}
    for m in _RENDERED_HEADING.finditer(rendered):
        rend.setdefault("company", []).append(m.group("company").strip())
    for m in _RENDERED_LOCATION.finditer(rendered):
        rend.setdefault("location", []).append(m.group("location").strip())
    for m in _RENDERED_URL.finditer(rendered):
        rend.setdefault("url", []).append(m.group("url"))

    # SCOPE and VALUES are different questions, and conflating them cost a hole in each
    # direction. The per-half lists above answer "is this half still being swept"; they must
    # stay per-half, or a missing `**URL:**` line is covered for by the frontmatter key (that
    # was the defect the halves were split to fix). This third list answers "is every url in
    # the note reserved", and it must span the WHOLE block: scoped to `rendered`, a real host
    # in an EXTRA frontmatter field -- `applied_url:`, say, which no per-key pattern names --
    # bypassed `_is_reserved` entirely while the ordinary `url:` kept both scope checks happy.
    # Measured, both directions. Fixing this by pointing the rendered sweep at the whole block
    # (the shape first proposed in review) closes the value gap and reopens the scope one.
    return {"frontmatter": fm, "rendered": rend,
            "urls": [m.group("url") for m in _RENDERED_URL.finditer(blocks[0])]}


def _readme_note_values(key: str) -> list:
    """Every value for `key`, both halves, for the roster checks."""
    halves = _readme_sample_note_fields()
    return halves["frontmatter"].get(key, []) + halves["rendered"].get(key, [])


def test_readmes_illustrative_lead_note_uses_reviewed_identities():
    """An employer, a place or a real host anywhere in README's sample note ships to PyPI.

    "Anywhere" is load-bearing: the note states each identity twice, once as a frontmatter key
    and once rendered below the closing `---`, and an earlier cut of this guard read only the
    first. Both halves are swept now.

    Nothing local can tell a real employer from an invented one, so this is a RATCHET like the
    sweeps above rather than a classifier: a new value fails until a human rules on it. The URL
    half is decidable without a human -- RFC 2606 reserves the TLDs -- so it is structural.
    """
    halves = _readme_sample_note_fields()

    # SCOPE, PER HALF. Every identity must be found in BOTH halves, because each half is swept
    # by its own patterns and either can be lost on its own. Merged into one list this was
    # nearly inert: measured, ALL SIX half-removals (company/location/url x frontmatter/
    # rendered) left the guard green, since the surviving half kept the key non-empty. A
    # frontmatter `company:` renamed to `employer:` would then carry a real employer with
    # nothing sweeping it at all.
    for half in ("frontmatter", "rendered"):
        for key in ("company", "location", "url"):
            assert halves[half].get(key), (
                f"README's sample lead note has no {key} in its {half} half -- that half's "
                f"sweep is enumerating nothing, so anything placed there would go unchecked "
                f"while the other half kept this assertion satisfied")

    fields = {k: halves["frontmatter"].get(k, []) + halves["rendered"].get(k, [])
              for k in ("company", "location")}
    # Values come from the whole-block sweep, which is a SUPERSET of what the halves found --
    # asserted, so this cannot silently narrow back to one half's view.
    fields["url"] = halves["urls"]
    per_half_urls = set(halves["frontmatter"].get("url", [])) | set(halves["rendered"].get("url", []))
    assert per_half_urls <= set(fields["url"]), (
        f"the whole-block url sweep missed {sorted(per_half_urls - set(fields['url']))}, which "
        f"the per-half sweeps found -- it is meant to cover at least everything they do")

    unreviewed = sorted({c for c in fields["company"] if c not in _REVIEWED_FIXTURE_IDENTITIES})
    assert not unreviewed, (
        f"README's sample lead note names {unreviewed}, which no human has reviewed. Nothing "
        f"here can tell a real employer from an invented one, and README ships to PyPI -- so "
        f"a person must rule on it and add it to _REVIEWED_FIXTURE_IDENTITIES.")

    unreviewed_places = sorted(
        {p for p in fields["location"] if p not in _README_REVIEWED_LOCATIONS})
    assert not unreviewed_places, (
        f"README's sample lead note is located in {unreviewed_places}, which no human has "
        f"reviewed. A real place here reads as one person's hunt geography (#27), in the file "
        f"published to PyPI -- rule on it and add it to _README_REVIEWED_LOCATIONS.")

    for url in fields["url"]:
        # urlsplit, not a hand regex: the first cut captured USERINFO into the host, so
        # `https://example.invalid@real.example.com/x` read as reserved. urlsplit knows the
        # difference, and strips the port too.
        host = urlsplit(url).hostname or ""
        assert host, f"README's sample note has an unparseable url: {url!r}"
        assert _is_reserved(host), (
            f"README's sample lead note points at {host!r}, which is not an RFC 2606 reserved "
            f"domain. A real host in the PyPI description reads as a real posting.")


def test_the_readme_location_roster_carries_no_value_the_note_stopped_using():
    """The reverse direction, which `_README_REVIEWED_LOCATIONS` was missing.

    Same reasoning as `test_the_corpus_rosters_carry_no_value_the_fixtures_stopped_using`
    above, and it applies harder here: this roster has ONE member and two references, so it is
    the cheapest place in the file to append a value and the likeliest to accumulate an
    approval nobody remembers granting. A roster that outlives the note it approves says a
    human ruled on something that is no longer shipping.
    """
    stale = _README_REVIEWED_LOCATIONS - set(_readme_note_values("location"))
    assert not stale, (
        "_README_REVIEWED_LOCATIONS names values README's sample lead note no longer uses:\n  "
        + "\n  ".join(sorted(map(repr, stale)))
        + "\n\nDrop them, so the roster stays a list of what is actually shipping.")


# The scanner in `tests/markdown_fences.py` is the one place fence rules are implemented, so
# this is where they are checked against the SPEC. It replaces an agreement test that compared
# the two old duplicated patterns to each other: they agreed, and were both wrong, which is the
# failure a pattern-to-pattern comparison cannot see and a spec-derived table can.
#
# A row per CommonMark 4.5 clause, not per shape anyone remembered. Note the expected column
# for an UNCLOSED fence: the block runs to end of document, so its content IS inside it and IS
# stripped -- the protection callers rely on is `unclosed_fence` refusing, never the content
# happening to survive. Writing that column from the old regexes' behaviour instead of from the
# spec is what hid the mixed-delimiter closer bug, which this table then caught.
_FENCE_SHAPES = (
    # name,                          document,                              stripped, unclosed
    ("plain backtick pair",          "a\n```md\nX\n```\nb\n",              True,  False),
    ("plain tilde pair",             "a\n~~~md\nX\n~~~\nb\n",              True,  False),
    ("four open four close",         "a\n````md\nX\n````\nb\n",            True,  False),
    ("four open five close",         "a\n````md\nX\n`````\nb\n",           True,  False),
    ("three open four close",        "a\n```md\nX\n````\nb\n",             True,  False),
    ("four open THREE close",        "a\n````md\nX\n```\nb\n",             True,  True),
    ("mixed-delimiter closer",       "a\n```md\nX\n```~\nb\n",             True,  True),
    ("tilde open backtick close",    "a\n~~~md\nX\n```\nb\n",              True,  True),
    ("unterminated",                 "a\n```md\nX\n",                      True,  True),
    ("indented three both lines",    "a\n   ```md\nX\n   ```\nb\n",        True,  False),
    ("indented open flush close",    "a\n   ```md\nX\n```\nb\n",           True,  False),
    ("flush open indented close",    "a\n```md\nX\n   ```\nb\n",           True,  False),
    ("four spaces is not a fence",   "a\n    ```md\nX\n    ```\nb\n",      False, False),
    ("backtick in backtick info",    "a\n```js `no`\nX\n```\nb\n",         False, True),
    ("backtick in TILDE info is ok", "a\n~~~js `ok`\nX\n~~~\nb\n",         True,  False),
    ("closer may carry no info",     "a\n```md\nX\n``` info\n```\nb\n",   True,  False),
    # CommonMark 2.1: a line ending is \n, \r, or \r\n. Splitting on \n alone left a
    # trailing \r that no closing-fence pattern accepts, so a well-formed CRLF block read
    # as UNCLOSED. Not reachable from this repo's callers (universal newlines translate on
    # text reads, measured), but the scanner is general and the rows are per spec clause.
    ("CRLF pair",                    "a\r\n```md\r\nX\r\n```\r\nb\r\n",   True,  False),
    ("CRLF unterminated",            "a\r\n```md\r\nX\r\n",              True,  True),
    ("lone CR pair",                 "a\r```md\rX\r```\rb\r",             True,  False),
)


def test_the_fence_scanner_matches_commonmark():
    """Both README guards depend on this; a missed rule is a bypass in each of them.

    Every row is a CommonMark clause rather than a case someone hit in review -- which is the
    difference between a check that generalises and three consecutive rounds of adding the
    shape a reviewer just named.
    """
    from tests.markdown_fences import strip_fenced_blocks

    for name, doc, want_stripped, want_unclosed in _FENCE_SHAPES:
        assert ("X" not in strip_fenced_blocks(doc)) == want_stripped, (
            f"strip_fenced_blocks gets {name!r} wrong (expected stripped={want_stripped})")
        assert unclosed_fence(doc) == want_unclosed, (
            f"unclosed_fence gets {name!r} wrong (expected {want_unclosed})")

    # SCOPE, both columns: a table whose rows all expect the same answer would pass over a
    # scanner that always strips, or never reports an unclosed fence.
    assert len({s for _, _, s, _ in _FENCE_SHAPES}) == 2, "the stripped column tests one outcome"
    assert len({u for _, _, _, u in _FENCE_SHAPES}) == 2, "the unclosed column tests one outcome"
