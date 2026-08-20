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

SCOPE, stated honestly. This sweeps four ENUMERATED positions that carry a lead identity:
frontmatter `company:`, lead-note filenames, `lead_slug=` kwargs, and the first positional
argument of the `_note`/`_lead`/`_vault_with`/`_shortlist_with` helpers. That last position
holds a company in some modules and a slug in others — the same helper name has different
signatures per file — which is why the roster is named for IDENTITIES rather than companies:
a leaked employer name could land in either shape, so both are swept and neither is filtered
out. A name written into some other shape — prose in a comment, a docstring, an unusual
helper — is NOT covered; the email-domain guard below is the broader net.

A FIFTH collector, added for #133/#107, sweeps a different category entirely: the
equal-opportunities/protected-characteristic fields (`sluice/apply/packet.py`'s `_WARNED_KEYS`
-- ethnicity, religion, disability, gender identity and similar special-category personal
data). SCOPE, stated with the same honesty as the four collectors above: it matches FOUR
fixture shapes a warned-field value can appear in -- a double- or single-quoted dict-literal
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

A SIXTH collector closes what this docstring previously recorded as an accepted gap
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
"""
import dataclasses
import itertools
import re
from pathlib import Path

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
_REVIEWED_FIXTURE_IDENTITIES = frozenset({
    "A", "A-B", "Acme", "Alpha", "Aye", "B", "Beavni", "Bee", "Beta", "C", "Conflicted",
    "D", "Delta", "Epsilon", "Example", "Example Analytics", "Example Beta", "Example Co",
    "Example Foundry", "Example Ltd", "Example Meridian", "Example MeridianRemote",
    "Example Northgate",
    "Example Systems", "Example Telemetry", "Example Tidal", "Foo", "Gamma",
    "Human Typed Co", "N-A", "Unknown", "Widget", "X",
    "a", "a1", "a2", "b", "b1", "b2", "blank", "c", "d", "example-lead",
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
)

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
    LIVE instance (`f"Author-email: MrReasonable <{...}>"`, an f-string's own source). The
    other five collect no mid-string interpolation today, so applying the predicate to
    them changes nothing now and closes the same latent hole -- which is the point, since
    the alternative is four sites drifting apart again.
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
# for free -- exactly the same reasoning that motivates it for the four identity
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


def _all_fixture_identities():
    names = set()
    for _label, pattern in _IDENTITY_COLLECTORS:
        names |= _collect(pattern)
    return names


@pytest.mark.parametrize("label,pattern", _COLLECTORS, ids=[c[0] for c in _COLLECTORS])
def test_every_collector_actually_finds_fixtures(label, pattern):
    """A collector that matches nothing makes EVERY check built on it VACUOUSLY green.

    This is the failure mode that matters. A regex silently stops matching (a helper is
    renamed, a quoting style changes), the sweep finds an empty set, and the guard reports
    success while covering nothing — "a search that finds nothing proves nothing." The floor
    is deliberately low: it pins that the position is still LIVE, not how many fixtures the
    repo happens to have.

    Where each collector's result GOES differs, and the split is what the counts below
    describe. The four `_IDENTITY_COLLECTORS` feed `_all_fixture_identities()` and the
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
    assert len(_COLLECTORS) == 6, (
        f"{len(_COLLECTORS)} collectors, but the docstring of "
        "test_every_collector_actually_finds_fixtures describes six -- update the prose "
        "and this number together")
    assert len(_IDENTITY_COLLECTORS) == 4, (
        f"{len(_IDENTITY_COLLECTORS)} collectors feed the employer roster, but that same "
        "docstring says four -- a collector moved between the two groups, so the prose "
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
    stale = sorted(_REVIEWED_FIXTURE_IDENTITIES - _all_fixture_identities())
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
