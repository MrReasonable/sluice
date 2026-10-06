# tests/test_cv_bundle.py
import re

import pytest

from sluice.core.protocols import CvLayout, LayoutRole
from sluice.cv import bundle as B
from sluice.cv.validate import entry_facts

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

# The two skill values below are on tests/test_fixture_name_neutrality.py's

# The frozen literal below is the bundle text as the auditor saw it BEFORE #174's refactor,
# kept as the one independent reference `_oracle` reads. Its baseline block is no longer
# anyone's pool (#364 D2: the baseline is not read), so it stays only as that literal's
# provenance.
#
# This is the reference for the allowlist guards below, and it is load-bearing that it is
# a LITERAL rather than something recomputed: a reference derived from the code moves with
# any mutation of that code, which is exactly how an earlier revision shipped three tests
# that killed nothing (measured -- see the #174 design doc's D10).
#
# Every entry field carries a distinct sentinel digit, INCLUDING `best_for` and `category`,
# which `_entry_block` does not emit -- so the equality below asserts their exclusion
# rather than merely failing to mention it. The third entry has no body, covering
# `_entry_block`'s one-line arm.
#
# Updating this literal is a DELIBERATE act: it means what an entry contributes to the
# prompt has changed. Re-capture it, read the diff, and say in the commit message why.

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
FROZEN_NEGATIVES = ["never claim 91 users", "never claim 92 uptime"]
FROZEN_PREFIX_MAP = {"Example Alpha 31": "AL", "Example Beta 41": "BE",
                      "Example Alpha 51": "AL"}   # keys are the FULL company strings:
# `_prefix` does `prefix_map.get(company) or company`, so a key that is a PREFIX of the
# company falls through to deriving "EX" from the name and every id collides into one
# sequence. Measured. This module's own header comment already documents the trap.

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
    b = B.build_bundle(ENTRIES, ["Example Decoy"], ["leadership"], PREFIX)
    # all 3 entries present even though only 2 match the keyword
    assert len(b["entries"]) == 3
    # leadership-matching entries rank first
    assert b["entries"][0]["best_for"] == "leadership"

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



# Placement does not change an entry's figures, so any layout will do for the pool guards.
_ANY_LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha 31", "01/2020", "present"),))


def _figures(bundle):
    return {eid: f.figures for eid, f in entry_facts(bundle, _ANY_LAYOUT).items()}


def _frozen_bundle():
    """The frozen entries, negatives and inventory, as `build_bundle` shapes them."""
    return B.build_bundle(FROZEN_ENTRIES, FROZEN_NEGATIVES, [], FROZEN_PREFIX_MAP,
                          skills=FROZEN_SKILLS)


def _oracle(bundle_text):
    """`_bundle_ids_and_nums` as it stood in sluice/cv/validate.py before #174 deleted it.

    Transcribed from `git show f1c4e7f:sluice/cv/validate.py` lines 52-77. Two changes
    from that transcription, both non-substantive: `nums` values are frozen to
    `frozenset` to compare against `entry_facts`' figures, and the pre-change function's third
    return value, `ids` (a dict of id -> the full matched line, assigned in the same
    branch as `nums`), is dropped -- its key set is provably identical to `nums`'s, since
    both are written together in that one `if m:` branch and nothing later adds to either
    independently, so keeping it here would add nothing this test can observe. Every
    predicate -- both regexes, the `continue`, the three branches -- is byte-for-byte the
    pre-change code.

    Deriving this reference by reading the NEW code would assert that the code equals
    itself and certify nothing. Feeding it a rendering of the bundle would do the same one
    level out, because the rendering is itself under test here: measured, `drop_title`,
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
    """The co-variant detector, retargeted (#364 spec §12.2): `_entry_block` feeds both the
    composer's text and each entry's figures, so a field dropped from it would leave any
    render-versus-figures comparison agreeing. The frozen literal, captured before the
    change, is what makes the loss visible. Its baseline block is no longer anyone's pool:
    the baseline is not read (#364 D2).

    The corpus is CLEAN on purpose. On POISONED input (an `[XX9]`-shaped line inside an
    entry body) the two are deliberately UNEQUAL -- that inequality is the entire point of
    #174, and a future reader must not "repair" this by widening the corpus."""
    nums, _baseline = _oracle(FROZEN_BUNDLE_TEXT)
    assert _figures(_frozen_bundle()) == nums


def test_entry_facts_sentinels_hold_independent_of_the_frozen_literal():
    """Compares against NO literal, so re-freezing cannot bring it back into sync. The
    frozen-literal guard above can be laundered by re-capturing the literal after a
    NARROWING (drop `title` from `_entry_block` and re-freeze, and it stays green); this
    one names the sentinel digits directly, keyed to the FIELD each came from."""
    f = _figures(_frozen_bundle())
    # AL1: company (31), title (32), metrics (33, 34), body (37, 38). Losing '32' alone is
    # what a dropped `title` field would cost.
    assert {"31", "32", "33", "34", "37", "38"} <= f["AL1"]
    # best_for (35) and category (36) are never emitted, so never a figure.
    assert not ({"35", "36"} & f["AL1"])
    # BE1: company (41), metrics (43), body (47). Its title has no digit of its own, so
    # AL1's '32' is what witnesses title's presence.
    assert {"41", "43", "47"} <= f["BE1"] and not ({"45", "46"} & f["BE1"])
    # AL2 has no body, best_for or category: equality, since nothing else could be admitted.
    assert f["AL2"] == frozenset({"51", "53"})
    every = set().union(*f.values())
    assert not ({"71", "72"} & every)                         # the Skills Inventory's figures
    assert not ({"91", "92"} & every)                         # the negatives'
    assert not ({"21", "22"} & every)                         # the baseline's: read by nothing


def test_a_duplicate_id_raises_naming_the_id_and_not_the_entry():
    """`assign_codes` cannot produce a duplicate, so this is unreachable from
    `build_bundle`. It earns its lines because `entry_facts` takes an untyped dict and
    #164 gave bundle contents a non-human author -- and because the failure it prevents is
    one entry's allowlist silently replacing another's, which is #174's own defect shape one
    layer up.

    The message must name the ID and no part of the ENTRY: an entry carries the user's
    company, title, metrics and body, and cv/engine.py::run_batch logs a failed run with %s.
    """
    b = {"negatives": [],
         "entries": [{"id": "AL1", "company": "Example Alpha", "title": "A",
                      "metrics": "1", "body": "secret body text"},
                     {"id": "AL1", "company": "Example Beta", "title": "B",
                      "metrics": "2", "body": "other secret"}]}
    with pytest.raises(ValueError) as ei:
        entry_facts(b, _ANY_LAYOUT)
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


def test_a_multi_word_keyword_matches_the_entry_that_answers_it():
    """Both sides must go through the SAME tokenise-then-stem operation. Stemming a
    keyword WHOLE gives `_stem("machine learning") == "machine learn"`, which no tokenised
    haystack can contain -- measured, the entry answering it ranked LAST of seven while
    entries matching an unrelated keyword scored 1.

    Not reachable from `cv/engine.py:_jd_keywords`, which yields single `[a-z]{4,}` words;
    this pins `rank`'s own contract, since it is reachable with any keyword list."""
    entries = ([_rank_entry("delivery", f"unrelated-{i}") for i in range(3)]
               + [_rank_entry("machine learning", "THE-RIGHT-ONE")]
               + [_rank_entry("delivery", f"unrelated-{i}") for i in range(3, 6)])
    ranked = B.rank(entries, ["machine learning", "delivery"])
    assert ranked[0]["title"] == "THE-RIGHT-ONE", [e["title"] for e in ranked]


def test_single_word_keywords_are_unaffected_by_the_tokenising():
    """The production path. Whole-string stemming and tokenised stemming agree exactly on
    single alphabetic words, so this change cannot have moved any real ranking."""
    from sluice.core.stem import stem, stem_all

    for kws in (["documenting"], ["documenting", "delivery"], []):
        assert {stem(k) for k in kws} == stem_all(" ".join(kws)), kws


def test_the_substring_false_positives_are_gone():
    """`"java" in "javascript"` is True, so the old ranker scored a JavaScript entry on a
    Java keyword. Stems do not relate them."""
    entries = [_rank_entry("javascript", "js"), _rank_entry("java", "java")]
    assert B.rank(entries, ["java"])[0]["title"] == "java"




# ── #165: the Skills Inventory as a non-citable section ──────────────────────
_SKILL = {"title": "Example Cloud Skill", "best_for": "platform documentation",
          "company": "", "category": "", "metrics": "", "body": "Body prose.",
          "fields": {"Proficiency": "8 years", "Domain": "platform documentation",
                     "Evidence": "shipped 62 things", "Signal Value": "depth not breadth"}}



def _bundle_with_skills(skills=(_SKILL,)):
    return B.build_bundle(FROZEN_ENTRIES, FROZEN_NEGATIVES, [], FROZEN_PREFIX_MAP,
                          skills=list(skills))


def test_a_skills_digit_is_licensed_in_neither_pool():
    """THE load-bearing #165 row, against no literal: the skill's own figures (8, 62) may
    license nothing -- not an entry's figures, and (tests/test_cv_checks.py) not the
    profile's."""
    every = set().union(*_figures(_bundle_with_skills()).values())
    assert not ({"8", "62"} & every), (
        "a skills digit reached an entry's figures -- the framing lines have been folded "
        "into _entry_block, which licenses them for that entry")


def test_the_composer_bundle_carries_every_entry_and_the_framing():
    """Everything the entries section emits must survive into the composer's text, or a
    source has been lost rather than a section added."""
    composer = B.render_structured_bundle(_bundle_with_skills())
    for fragment in (B._ENTRIES_HEADER_PROMPT, "[AL1]", "[BE1]", "[AL2]",
                     B._INVENTORY_HEADER_PROMPT, B._GUIDANCE_HEADER_PROMPT):
        assert fragment in composer, fragment


def test_the_skills_section_renders_after_the_entries_and_before_the_guidance():
    text = B.render_structured_bundle(_bundle_with_skills())
    assert text.index("[AL2]") < text.index(B._INVENTORY_HEADER_PROMPT) \
           < text.index(B._GUIDANCE_HEADER_PROMPT)


def test_the_skills_section_carries_the_four_fields_and_the_body():
    text = B.render_structured_bundle(_bundle_with_skills())
    for fragment in ("Example Cloud Skill", "proficiency=8 years",
                     "signal=depth not breadth", "shipped 62 things", "Body prose."):
        assert fragment in text, fragment


def test_the_framing_reads_the_kinds_own_declared_fields():
    """`_framing_lines` hard-codes four frontmatter names, and nothing tied them to the
    registry that declares them. A coordinated rename in `EVIDENCE_KINDS["skills"].fields`
    would degrade the section to bare `- title` lines and stay green, because every other
    framing test hand-builds the `fields` dict with the same four literals it is checking.

    Asserts the SET, not a subset: a field dropped from either side is what this catches."""
    from sluice.core.protocols import EVIDENCE_KINDS

    declared = set(EVIDENCE_KINDS["skills"].fields)
    assert declared == {"Proficiency", "Domain", "Evidence", "Signal Value", "Label"}, (
        "the skills kind's fields changed; sluice/cv/bundle.py:_framing_lines reads them "
        "by name and must change with them")
    # Every declared field, given a distinct value, must reach the rendered section.
    marked = {k: f"value-for-{k.lower().replace(' ', '-')}" for k in declared}
    text = B.render_structured_bundle(_bundle_with_skills(
        skills=({"title": "Example Data Skill", "best_for": "", "body": "",
                 "fields": marked},)))
    for key, value in marked.items():
        assert value in text, f"{key} is declared by the registry but never rendered"


def test_a_real_vault_read_renders_through_the_composer_bundle(tmp_path):
    """The round trip nothing else covers: every other framing test hand-builds the entry
    dict that `Vault.read_evidence` is supposed to produce, so a change to the store's
    shape (the `fields` key, the floor mapping) would leave them all green while the real
    path rendered nothing."""
    import os

    from sluice.core.vault import Vault

    sk = tmp_path / "Job Applications" / "Skills Inventory"
    os.makedirs(sk)
    (sk / "Example Data Skill.md").write_text(
        "---\nProficiency: 71 years\nDomain: platform\nEvidence: shipped 72 things\n"
        "Signal Value: depth\nverified: 2026-08-25\n---\nBody prose.\n", encoding="utf-8")
    entries = Vault(str(tmp_path)).read_evidence("skills", verified_only=True)
    assert entries, "the store returned nothing; the rest of this test would be vacuous"

    b = B.build_bundle(FROZEN_ENTRIES, FROZEN_NEGATIVES, [], FROZEN_PREFIX_MAP, skills=entries)
    text = B.render_structured_bundle(b)
    for fragment in ("Example Data Skill", "proficiency=71 years", "domain=platform",
                     "signal=depth", "shipped 72 things", "Body prose."):
        assert fragment in text, fragment
    # ...and the store's own figures are still licensed nowhere.
    assert not ({"71", "72"} & set().union(*_figures(b).values()))


def test_an_empty_inventory_emits_no_header_at_all():
    """Not an empty header: that asserts to the model that the candidate has no skills,
    which is a negative claim it may act on. Empty means abstain."""
    assert B._INVENTORY_HEADER_PROMPT not in B.render_structured_bundle(
        _bundle_with_skills(skills=()))


def test_the_derived_constraint_reaches_no_number_pool():
    """#31: the guidance -- the negatives and the derived constraint -- is shown to the model
    and is deliberately not a source."""
    b = B.build_bundle(FROZEN_ENTRIES, ["never claim 91 users"], [], FROZEN_PREFIX_MAP,
                       skills=[_SKILL])
    assert not ({"91"} & set().union(*_figures(b).values()))
    text = B.render_structured_bundle(b)
    # sluice's own rule precedes the guidance and so is never read as part of it.
    assert text.index(B._TOOLS_SOURCE_PROMPT) < text.index(B._GUIDANCE_HEADER_PROMPT)


def test_the_derived_constraint_names_the_same_claim_sources_as_the_prompt_rule():
    """Both strings land in the SAME prompt, so a source one names and the other does not is a
    contradiction the composer can only resolve by guessing. Reads the REAL rule text."""
    from sluice.cv.compose import _STRUCTURED_RULES_PROMPT
    assert "VERIFIED EXPERIENCE ENTRIES" in B._TOOLS_SOURCE_PROMPT
    assert "SKILLS INVENTORY" not in B._TOOLS_SOURCE_PROMPT
    for text in (B._TOOLS_SOURCE_PROMPT, _STRUCTURED_RULES_PROMPT):
        assert "BASELINE" not in text       # #364 D2: the baseline is not read when composing
    assert ("The VERIFIED EXPERIENCE ENTRIES are the ONLY permitted source for the profile "
            "and the bullets") in _STRUCTURED_RULES_PROMPT


def test_the_derived_constraint_names_no_skill_and_so_cannot_go_stale():
    """A cross-reference, not a generated roster: a roster would duplicate the SKILLS
    section immediately above it and grow without bound."""
    assert "Example Cloud" not in B._TOOLS_SOURCE_PROMPT
    assert "platform" not in B._TOOLS_SOURCE_PROMPT


def test_layout_dates_and_headings_reach_no_figure_pool():
    # #364 spec §12.2's new exclusion: the layout is the user's, and its digits license nothing.
    layout = CvLayout(roles=(LayoutRole("Example Northgate 2020", "01/2019", "06/2021",
                                        employers=("Example Alpha",)),))
    b = B.build_bundle([{"title": "Ran it", "company": "Example Alpha", "metrics": "",
                         "body": "Ran it."}], [], [], {})
    assert [f.figures for f in entry_facts(b, layout).values()] == [frozenset()]


@pytest.mark.parametrize("value", ["Example 9001", "Example 2.0", "9E modelling",
                                   "5X", "123.45ab"])
def test_a_digit_leading_skill_token_stays_refused_whatever_it_names(value):
    """The STATED over-refusal, carried onto `Tools:` (#364 spec §4.2, §13): a word-then-number
    name is structurally the metric shorthand `Result 92`, so it is refused, and said so.

    The values are synthetic, and the real names they stand for live in `docs/USAGE.md`,
    where a user reads WHICH of their own credentials this rule costs them. Only the token
    shape is under test here."""
    from sluice.core.tokens import tool_items
    with pytest.raises(ValueError, match="must begin with a letter"):
        tool_items({"fields": dict(Tools=value)})
