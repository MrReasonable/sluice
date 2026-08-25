# tests/test_cv_bundle.py
import re

import pytest

from sluice.cv import bundle as B

# An explicit map is REQUIRED for any fixture using more than one `Example ...`
# company: `_prefix` derives its code from the first two letters, so every one of
# them derives to "EX" and they would share a sequence (EX1/EX2/EX3) rather than
# getting one each. That fails loudly here, but a new fixture that omits the map
# would be asserting against codes it did not intend.
PREFIX = {"Example Systems": "ES", "Example Foundry": "EF", "Example Telemetry": "ET"}
ENTRIES = [
    {"title": "Grew team", "company": "Example Foundry", "best_for": "leadership",
     "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
    {"title": "Shipped MVP", "company": "Example Systems", "best_for": "delivery",
     "category": "delivery", "metrics": "3 months", "body": "Concept to live."},
    {"title": "Was CTO", "company": "Example Telemetry", "best_for": "leadership",
     "category": "leadership", "metrics": "15", "body": "Led 15 people."},
]

# The bundle prompt as it stood BEFORE #174's refactor, frozen as a literal.
#
# This is the reference for `tests/test_cv_bundle.py`'s two assertions, and it is
# load-bearing that it is a LITERAL rather than something recomputed: a reference derived
# from `render_bundle` moves with any mutation of `render_bundle`, which is exactly how
# this spec's revision 2 shipped three tests that killed nothing (measured -- see the
# design doc's D10). Captured from the shipped implementation at the commit that precedes
# the refactor.
#
# Every entry field carries a distinct sentinel digit, INCLUDING `best_for` and `category`,
# which `render_bundle` does not emit -- so the equality below asserts their exclusion
# rather than merely failing to mention it. The third entry has no body, covering
# `_entry_block`'s one-line arm.
#
# Updating this literal is a DELIBERATE act: it means the prompt the model sees has
# changed. Re-capture it, read the diff, and say in the commit message why the prompt moved.

FROZEN_ENTRIES = [
    {"company": "Example Alpha 31", "title": "Staff Engineer, 32 teams",
     "metrics": "33 34", "best_for": "leadership 35", "category": "platform 36",
     "body": "Ran 37 services.\nOwned 38 dashboards."},
    {"company": "Example Beta 41", "title": "Principal Engineer",
     "metrics": "43", "best_for": "delivery 45", "category": "data 46",
     "body": "Cut latency to 47 ms."},
    {"company": "Example Alpha 51", "title": "Engineer", "metrics": "53",
     "best_for": "", "category": "", "body": ""},
]
FROZEN_BASELINE = "Baseline names 21 and 22."
FROZEN_NEGATIVES = ["never claim 91 users", "never claim 92 uptime"]
FROZEN_PREFIX_MAP = {"Example Alpha 31": "AL", "Example Beta 41": "BE",
                      "Example Alpha 51": "AL"}   # keys are the FULL company strings:
# `_prefix` does `prefix_map.get(company) or company`, so a key that is a PREFIX of the
# company falls through to deriving "EX" from the name and every id collides into one
# sequence. Measured. tests/test_cv_bundle.py:8-12 already documents this trap.

FROZEN_BUNDLE_TEXT = """\
=== BASELINE CV (authoritative for dates/employers/certs) ===
Baseline names 21 and 22.

=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ===
[AL1] (Example Alpha 31) Staff Engineer, 32 teams | metrics=33 34
Ran 37 services.
Owned 38 dashboards.

[BE1] (Example Beta 41) Principal Engineer | metrics=43
Cut latency to 47 ms.

[AL2] (Example Alpha 51) Engineer | metrics=53

=== NEGATIVE CONSTRAINTS (must NOT appear) ===
- never claim 91 users
- never claim 92 uptime"""

def test_codes_are_short_company_prefixed_and_sequenced():
    coded = B.assign_codes(ENTRIES, PREFIX)
    by_co = {e["company"]: e["id"] for e in coded}
    assert by_co["Example Foundry"] == "EF1"
    assert by_co["Example Systems"] == "ES1"
    assert by_co["Example Telemetry"] == "ET1"

def test_full_set_included_ranking_orders_not_excludes():
    b = B.build_bundle(ENTRIES, "BASELINE", ["Example Decoy"], ["leadership"], PREFIX)
    # all 3 entries present even though only 2 match the keyword
    assert len(b["entries"]) == 3
    # leadership-matching entries rank first
    assert b["entries"][0]["best_for"] == "leadership"

def test_render_bundle_has_codes_and_negatives_and_bodies():
    b = B.build_bundle(ENTRIES, "BASELINE CV TEXT", ["No Example Decoy Health."], ["leadership"], PREFIX)
    text = B.render_bundle(b)
    assert "BASELINE CV TEXT" in text
    assert "[EF1] (Example Foundry) Grew team | metrics=3 8" in text
    assert "Grew from 3 to 8." in text            # body included for the number gate
    assert "No Example Decoy Health." in text           # negatives block

def test_unknown_company_gets_two_letter_fallback():
    coded = B.assign_codes([{"title": "x", "company": "Acme Corp", "metrics": "", "body": ""}], {})
    assert coded[0]["id"] == "AC1"

def test_same_company_entries_are_sequenced_not_hardcoded_to_one():
    two_at_one_company = [
        {"title": "Grew team", "company": "Example Foundry", "best_for": "leadership",
         "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
        {"title": "Cut costs", "company": "Example Foundry", "best_for": "delivery",
         "category": "delivery", "metrics": "20%", "body": "Cut costs by 20%."},
    ]
    coded = B.assign_codes(two_at_one_company, PREFIX)
    assert [e["id"] for e in coded] == ["EF1", "EF2"]

def test_single_alpha_unmapped_company_still_yields_two_letter_code():
    # "4Z" is chosen for its SHAPE: exactly one alphabetic character, so _prefix
    # must pad rather than truncate. Any readable replacement loses the case.
    coded = B.assign_codes([{"title": "x", "company": "4Z", "metrics": "", "body": ""}], {})
    assert re.match(r"^[A-Z]{2}[0-9]+$", coded[0]["id"])

def test_prefix_map_override_is_coerced_to_two_letters():
    # a 1-char and a 3-char override must both become exactly-2-letter codes,
    # so a malformed prefix_map (e.g. from sluice.yaml) can never produce a
    # citation code that escapes the render-step strip regex.
    coded = B.assign_codes(
        [{"title": "x", "company": "Foo", "metrics": "", "body": ""},
         {"title": "y", "company": "Bar", "metrics": "", "body": ""}],
        {"Foo": "X", "Bar": "ABC"})
    ids = [e["id"] for e in coded]
    assert all(re.match(r"^[A-Z]{2}[0-9]+$", i) for i in ids), ids

# `Example Data` is on _REVIEWED_FIXTURE_IDENTITIES. The sentinels 71/72 collide with
# nothing already in FROZEN_BUNDLE_TEXT (which uses 21/22, 31-38, 41-47, 51-53, 91-92).
FROZEN_SKILLS = [{"title": "Example Data Skill", "best_for": "platform", "body": "",
                  "fields": {"Proficiency": "71 years", "Domain": "platform",
                             "Evidence": "shipped 72 things", "Signal Value": "depth"}}]

# The COMPOSER's prompt, which `render_bundle`'s freeze does not cover any more: since
# #165 the two audiences get different text, and this is the one the compose call
# receives. Frozen for the same reason the other is -- a refactor that changes
# presentation without changing any digit is otherwise invisible.
FROZEN_COMPOSER_BUNDLE_TEXT = """\
=== BASELINE CV (authoritative for dates/employers/certs) ===
Baseline names 21 and 22.

=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ===
[AL1] (Example Alpha 31) Staff Engineer, 32 teams | metrics=33 34
Ran 37 services.
Owned 38 dashboards.

[BE1] (Example Beta 41) Principal Engineer | metrics=43
Cut latency to 47 ms.

[AL2] (Example Alpha 51) Engineer | metrics=53

=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ===
- Example Data Skill | proficiency=71 years | domain=platform | signal=depth
  shipped 72 things

=== NEGATIVE CONSTRAINTS (must NOT appear) ===
- claim no technology, language, framework or tool that is not named in the BASELINE CV, the VERIFIED EXPERIENCE ENTRIES or the SKILLS INVENTORY above
- never claim 91 users
- never claim 92 uptime"""


def _frozen_bundle():
    return B.build_bundle(entries=FROZEN_ENTRIES, baseline=FROZEN_BASELINE,
                          negatives=FROZEN_NEGATIVES, jd_keywords=[],
                          prefix_map=FROZEN_PREFIX_MAP, skills=FROZEN_SKILLS)


def test_the_rendered_prompt_has_not_drifted():
    """`render_bundle`'s output IS the prompt the #60 ADVISORY audit sees
    (cv/engine.py:653). It used to be the compose prompt too; since #165 the composer gets
    `render_composer_bundle` and is frozen separately below. The pre-#174 text is frozen at
    the top of this file, so a refactor that changes presentation without changing any
    digit -- reordering fields, renaming `metrics=`, dropping the inter-entry blank line --
    is caught here rather than shipping a silently different prompt.

    Note `_frozen_bundle()` now CARRIES skills, and this literal is byte-identical to the
    one that predates them. That is D11 pinned at its strongest: the auditor's text is not
    merely free of a SKILLS header, it is unchanged from before the feature existed.

    Updating the literal is the deliberate act; failing this test is not a reason to
    weaken it."""
    assert B.render_bundle(_frozen_bundle()) == FROZEN_BUNDLE_TEXT


def test_the_composer_prompt_has_not_drifted():
    """The other live LLM call. Same rule, same reason: this exact text is what
    cv/engine.py hands compose(), and a presentation change that moves no digit is
    invisible to every other assertion in this file."""
    assert B.render_composer_bundle(_frozen_bundle()) == FROZEN_COMPOSER_BUNDLE_TEXT


def _oracle(bundle_text):
    """`_bundle_ids_and_nums` as it stood in sluice/cv/validate.py before #174 deleted it.

    Transcribed from `git show f1c4e7f:sluice/cv/validate.py` lines 52-77. Two changes
    from that transcription, both non-substantive: `nums` values are frozen to
    `frozenset` to compare against `BundleSources`, and the pre-change function's third
    return value, `ids` (a dict of id -> the full matched line, assigned in the same
    branch as `nums`), is dropped -- its key set is provably identical to `nums`'s, since
    both are written together in that one `if m:` branch and nothing later adds to either
    independently, so keeping it here would add nothing this test can observe. Every
    predicate -- both regexes, the `continue`, the three branches -- is byte-for-byte the
    pre-change code.

    Deriving this reference by reading the NEW code would assert that the code equals
    itself and certify nothing. Feeding it `render_bundle(b)` would do the same one level
    out, because `render_bundle` is itself under test here: measured, `drop_title`,
    `drop_company` and `emit_best_for` ALL SURVIVE that spelling, since both sides of the
    equality move with the mutant. It is fed the FROZEN literal for that reason.
    """
    section_re = re.compile(r"^\s*={3,}[^=].*[^=]={3,}\s*$")
    id_re = re.compile(r"^\[([A-Z]{2}\d+)\]")
    nums, baseline = {}, set()
    cur, seen_id = None, False
    for line in bundle_text.splitlines():
        if section_re.match(line):
            cur = None
            continue
        m = id_re.match(line)
        if m:
            seen_id, cur = True, m.group(1)
            nums[cur] = set(re.findall(r"\d+", line[m.end():]))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
        elif not seen_id:
            baseline |= set(re.findall(r"\d+", line))
    return {k: frozenset(v) for k, v in nums.items()}, frozenset(baseline)


def test_the_allowlist_still_matches_the_frozen_prompt():
    """The co-variant detector, and the reason the reference is a frozen literal.

    `_entry_block` feeds BOTH the prompt and the allowlist, so deleting a field from it
    removes that field from both and any render-vs-sources comparison still agrees --
    measured across 24 scenarios, killed by nothing. Comparing against text captured
    BEFORE the refactor is what makes the loss visible: the frozen literal still contains
    the digits, the new derivation no longer yields them, and the equality breaks.

    The corpus is CLEAN on purpose. On POISONED input (an `[XX9]`-shaped line inside an
    entry body or the baseline) the two are deliberately UNEQUAL -- that inequality is
    the entire point of #174, and a future reader must not "repair" this by widening the
    corpus.
    """
    b = _frozen_bundle()
    assert B.bundle_sources(b) == B.BundleSources(*_oracle(FROZEN_BUNDLE_TEXT))


def test_ids_is_derived_from_nums():
    b = _frozen_bundle()
    s = B.bundle_sources(b)
    assert set(s.ids) == set(s.nums)
    assert set(s.ids) == {"AL1", "BE1", "AL2"}


def test_bundle_sources_sentinels_hold_independent_of_the_frozen_literal():
    """The two tests above compare against FROZEN_BUNDLE_TEXT, and that literal's own
    header comment invites re-capturing it after a deliberate change -- which is exactly
    what lets a NARROWING launder through unnoticed. Measured: drop `title` from
    `_entry_block` and re-capture the literal to match (recomputed via `render_bundle`
    under that same mutation), and BOTH `test_the_rendered_prompt_has_not_drifted` and
    `test_the_allowlist_still_matches_the_frozen_prompt` stay green -- only the older
    substring pin (`test_render_bundle_has_codes_and_negatives_and_bodies`, which asserts
    presentation, not the allowlist) reddens.

    This test compares against no literal at all, so there is nothing to bring back into
    sync by re-freezing: it asserts the sentinel digits directly against
    `bundle_sources(b)`, keyed to which FIELD each digit came from.
    """
    b = _frozen_bundle()
    s = B.bundle_sources(b)
    # AL1: every field's own sentinel digit must be present -- company (31), title (32),
    # metrics (33, 34), body (37, 38). Losing '32' alone is what a dropped `title` field
    # would cost; the other five are the OTHER fields' distinct sentinels.
    assert {"31", "32", "33", "34", "37", "38"} <= s.nums["AL1"]
    # The skills sentinels must be absent from EVERY pool. This assertion compares against
    # no literal, so re-freezing cannot bring it back into sync -- it is the one check a
    # re-capture cannot silently move (#165).
    for sentinel in ("71", "72"):
        assert sentinel not in s.baseline
        assert all(sentinel not in n for n in s.nums.values())
    # best_for (35) and category (36) must be ABSENT: render_bundle never emits either
    # field, and bundle_sources harvests exactly what render_bundle emits.
    assert not ({"35", "36"} & s.nums["AL1"])
    # BE1: company (41), metrics (43), body (47). Its title ("Principal Engineer") has no
    # digit of its own, so this entry cannot witness title's presence -- AL1's '32' above
    # is what does.
    assert {"41", "43", "47"} <= s.nums["BE1"]
    assert not ({"45", "46"} & s.nums["BE1"])
    # AL2: no body, best_for or category -- only company (51) and metrics (53). Equality,
    # not containment, since there is nothing else this entry could admit.
    assert s.nums["AL2"] == frozenset({"51", "53"})
    # baseline: 21 and 22, never the negatives' 91/92.
    assert s.baseline == frozenset({"21", "22"})
    # The negatives sentinels (91, 92) must be absent from EVERY entry's allowlist too,
    # not merely excluded from baseline.
    all_nums = set().union(*s.nums.values())
    assert not ({"91", "92"} & all_nums)


def test_a_duplicate_id_raises_naming_the_id_and_not_the_entry():
    """`assign_codes` cannot produce a duplicate, so this is unreachable from
    `build_bundle`. It earns its lines because `bundle_sources` takes an untyped dict and
    #164 is about to give bundle contents a non-human author -- and because the failure it
    prevents is one entry's allowlist silently replacing another's, which is #174's own
    defect shape one layer up.

    The message must name the ID and no part of the ENTRY: an entry carries the user's
    company, title, metrics and body, and cv/engine.py:795 logs a failed run with %s.
    """
    b = {"baseline": "", "negatives": [],
         "entries": [{"id": "AL1", "company": "Example Alpha", "title": "A",
                      "metrics": "1", "body": "secret body text"},
                     {"id": "AL1", "company": "Example Beta", "title": "B",
                      "metrics": "2", "body": "other secret"}]}
    with pytest.raises(ValueError) as ei:
        B.bundle_sources(b)
    assert "AL1" in str(ei.value)
    assert "secret body text" not in str(ei.value)
    assert "Example Alpha" not in str(ei.value)


# ── #165: ranking survives word forms ────────────────────────────────────────
def _rank_entry(best_for, title):
    return {"title": title, "company": "Example Co", "best_for": best_for,
            "category": "", "metrics": "", "body": ""}


def test_a_word_form_mismatch_no_longer_buries_the_right_entry():
    """#165's comment. The ad's top requirement was 'documenting'; the one entry that
    evidenced it said 'documentation'. `"documenting" in "documentation"` is False, so it
    scored zero and ranked BELOW every unrelated entry that matched a different ad word.

    The scores are the point. With competitors on "delivery planning" and two of three
    keywords matching them, the competitors score 2 and the right entry 1 -- so it stays
    last AFTER the fix too, and the test proves nothing in either direction. Measured with
    the real stemmer, these values give competitor (old 1, new 1) and right entry
    (old 0, new 2): position 6 of 7 before, 0 of 7 after.
    """
    entries = ([_rank_entry("delivery", f"unrelated-{i}") for i in range(3)]
               + [_rank_entry("documentation deliveries", "THE-RIGHT-ONE")]
               + [_rank_entry("delivery", f"unrelated-{i}") for i in range(3, 6)])
    ranked = B.rank(entries, ["documenting", "delivery"])
    assert ranked[0]["title"] == "THE-RIGHT-ONE", [e["title"] for e in ranked]


def test_ranking_orders_and_never_excludes():
    """The property the whole bundle rests on: JD keywords reorder, never filter."""
    entries = [_rank_entry("documentation", "a"), _rank_entry("nothing relevant", "b")]
    assert len(B.rank(entries, ["documenting"])) == 2


def test_the_substring_false_positives_are_gone():
    """`"java" in "javascript"` is True, so the old ranker scored a JavaScript entry on a
    Java keyword. Stems do not relate them."""
    entries = [_rank_entry("javascript", "js"), _rank_entry("java", "java")]
    assert B.rank(entries, ["java"])[0]["title"] == "java"


# ── #165: the Skills Inventory as a fourth, non-citable section ──────────────
_SKILL = {"title": "Example Cloud Skill", "best_for": "platform documentation",
          "company": "", "category": "", "metrics": "", "body": "Body prose.",
          "fields": {"Proficiency": "8 years", "Domain": "platform documentation",
                     "Evidence": "shipped 62 things", "Signal Value": "depth not breadth"}}


def _bundle_with_skills(skills=(_SKILL,)):
    return B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES,
                          [], FROZEN_PREFIX_MAP, skills=list(skills))


def test_a_skills_digit_is_licensed_in_neither_pool():
    """THE load-bearing test of this feature, and it compares against NO frozen literal --
    so re-capturing FROZEN_BUNDLE_TEXT cannot bring it back into sync. `8` and `62` are
    the skill's own figures; neither may become a permitted number anywhere."""
    s = B.bundle_sources(_bundle_with_skills())
    assert "62" not in s.baseline
    assert all("62" not in n for n in s.nums.values())
    assert all("8" not in n for n in s.nums.values()), (
        "a skills digit reached an entry's allowlist -- the framing lines have been "
        "folded into _entry_block, which licenses them for that entry")


def test_render_bundle_is_unchanged_by_a_skills_key():
    """D11, and the strongest form of it: the ADVISORY audit keeps calling `render_bundle`,
    so the guarantee is that this function does not notice `bundle["skills"]` at all.
    Compared against a bundle built WITHOUT skills -- asserting only the absence of the
    header would pass for a function that returned ''."""
    without = B.render_bundle(B.build_bundle(
        FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES, [], FROZEN_PREFIX_MAP))
    assert B.render_bundle(_bundle_with_skills()) == without


def test_the_composer_bundle_is_the_source_bundle_plus_framing():
    """The other half: everything `render_bundle` emits must survive into the composer's
    text, or a source has been lost rather than a section added."""
    composer = B.render_composer_bundle(_bundle_with_skills())
    for fragment in ("=== BASELINE CV", "[AL1]", "[BE1]", "[AL2]",
                     "=== VERIFIED EXPERIENCE ENTRIES", "=== NEGATIVE CONSTRAINTS",
                     "=== SKILLS INVENTORY"):
        assert fragment in composer, fragment


def test_the_skills_section_renders_after_the_entries_and_before_the_negatives():
    text = B.render_composer_bundle(_bundle_with_skills())
    assert text.index("[AL2]") < text.index("=== SKILLS INVENTORY") \
           < text.index("=== NEGATIVE CONSTRAINTS")


def test_the_skills_section_carries_the_four_fields_and_the_body():
    text = B.render_composer_bundle(_bundle_with_skills())
    for fragment in ("Example Cloud Skill", "proficiency=8 years",
                     "signal=depth not breadth", "shipped 62 things", "Body prose."):
        assert fragment in text, fragment


def test_an_empty_inventory_emits_no_header_at_all():
    """Not an empty header: that asserts to the model that the candidate has no skills,
    which is a negative claim it may act on. Empty means abstain."""
    empty = _bundle_with_skills(skills=())
    assert "SKILLS INVENTORY" not in B.render_composer_bundle(empty)
    assert B.render_composer_bundle(empty) == B.render_bundle(empty)


def test_the_derived_constraint_never_reaches_the_auditors_bundle():
    """THE reason it is passed as `extra` rather than stored on the bundle. It contains
    the literal string "SKILLS INVENTORY", so storing it in bundle["negatives"] -- which
    BOTH renderers read -- hands the auditor a sentence naming a source it cannot see,
    and the D11 widening arrives as prose instead of as a section."""
    b = B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, ["never claim 91 users"],
                       [], FROZEN_PREFIX_MAP, skills=[_SKILL])
    assert B._DERIVED_NEGATIVE_PROMPT not in b["negatives"]
    assert "SKILLS INVENTORY" not in B.render_bundle(b)
    assert B._DERIVED_NEGATIVE_PROMPT in B.render_composer_bundle(b)


def test_the_derived_constraint_appears_only_with_a_non_empty_inventory():
    assert B._DERIVED_NEGATIVE_PROMPT not in B.render_composer_bundle(
        _bundle_with_skills(skills=()))


def test_configured_negatives_survive_alongside_the_derived_one():
    """cv.negatives stays: an inventory cannot express a negative that is not about skills
    at all ('never claim a security clearance'). Both must reach the composer."""
    b = B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, ["never claim 91 users"],
                       [], FROZEN_PREFIX_MAP, skills=[_SKILL])
    composer = B.render_composer_bundle(b)
    assert B._DERIVED_NEGATIVE_PROMPT in composer
    assert "never claim 91 users" in composer


def test_the_derived_constraint_permits_every_source_the_prompt_permits():
    """It sits in the most strongly worded block in the prompt, so a source it forgets is
    a source the composer drops. compose._RULES permits the BASELINE CV and the VERIFIED
    EXPERIENCE ENTRIES; the SKILLS INVENTORY is named because it is visible and must be
    excluded from the CLAIM set without being excluded from the emphasis set."""
    for source in ("SKILLS INVENTORY", "VERIFIED EXPERIENCE ENTRIES", "BASELINE CV"):
        assert source in B._DERIVED_NEGATIVE_PROMPT, source


def test_the_derived_constraint_names_no_skill_and_so_cannot_go_stale():
    """A cross-reference, not a generated roster: a roster would duplicate the SKILLS
    section immediately above it and grow without bound."""
    assert "Example Cloud" not in B._DERIVED_NEGATIVE_PROMPT
    assert "platform" not in B._DERIVED_NEGATIVE_PROMPT


def test_the_derived_constraint_reaches_no_number_pool():
    """#31: the negatives block is shown to the model and is deliberately not citable.
    The derived line is prose in that block and must inherit that exactly."""
    s = B.bundle_sources(B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE,
                                        ["never claim 91 users"], [], FROZEN_PREFIX_MAP,
                                        skills=[_SKILL]))
    assert "91" not in s.baseline and all("91" not in n for n in s.nums.values())
