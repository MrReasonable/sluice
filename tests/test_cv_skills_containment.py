# tests/test_cv_skills_containment.py
"""#168: SKILLS as gated content.

Task 3 taught `section_spans` to collect a SKILLS region as a CONTIGUOUS BULLET RUN;
the five tests immediately below (`test_skills_lines_are_collected_into_their_own_
region` and its neighbours) pin that collection ALONE and never reach `validate()`'s
violation list -- three independent reviewers found a Critical defect in an earlier
version of exactly this mechanism, so each case is measured, not merely plausible.

Task 4 (further below) adds row 2: an emitted SKILLS item must appear in the bundle's
SOURCE TEXT (`BundleSources.source_tokens`), searched as a token SUBSEQUENCE per
source block, case-insensitively. It is the first task in this file where `validate()`
actually refuses something. Tasks 5 (row 1: a bullet's skill must belong to a cited
entry) and 6 (digit handling) reuse the five shared helpers defined below rather than
redefining them -- see each helper's own docstring.
"""
from sluice.cv import validate as V
from sluice.cv.bundle import build_bundle, bundle_sources

# The base fixture every test but the last builds from. `{tail}` lets each test append
# content AFTER the SKILLS section without re-typing the WORK EXPERIENCE / SKILLS
# preamble five times.
_CV = """PROFILE
I did the work.

WORK EXPERIENCE

Example Alpha
01/2020-01/2024 | LOCATION | Engineer
- Ran the rebuild [AL1]

SKILLS
- Example Query
- Example Framework
{tail}"""


def test_skills_lines_are_collected_into_their_own_region():
    _p, work, skills = V.section_spans(_CV.format(tail=""))
    assert [t.strip() for _n, t in skills] == ["- Example Query", "- Example Framework"]
    assert [t.strip() for _n, t in work] == ["- Ran the rebuild [AL1]"]


def test_a_publications_bullet_after_skills_is_still_citation_checked():
    """Direction 1, measured on main: this bullet IS citation-checked today. A SKILLS
    region that simply cleared `in_work` would swallow it, and a fabricated figure in it
    would never be number-checked."""
    tail = "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n"
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert any("92%" in t for _n, t in work)


def test_a_publications_bullet_after_certificates_is_still_uncited_clean():
    """Direction 2, measured on main: this bullet is NOT citation-checked today. The
    obvious repair for direction 1 -- revert to the WORK region on any unmodelled header
    -- regresses this one. Both directions are non-negotiable."""
    tail = ("\nCERTIFICATES\n- Example Practitioner\n"
            "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n")
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert not any("92%" in t for _n, t in work)


def test_a_blank_line_inside_the_run_does_not_eject_the_remaining_skills():
    """A blank between skill entries is ordinary formatting -- `cv/parse.py` tolerates it in
    three places. An earlier revision ended the run at the first NON-BULLET line, which put
    the following bullets back in WORK as UNCITED BULLET; since section 4.4 forbids
    per-skill `[id]`s, the retry's cheapest compliance was to ADD one, which is work-clean
    and still renders as a skill. This test pinned that launder as intended before the fix.
    """
    tail = ""
    cv = _CV.format(tail=tail).replace("- Example Framework", "\n- Example Framework")
    _p, work, skills = V.section_spans(cv)
    assert [t.strip() for _n, t in skills] == ["- Example Query", "- Example Framework"]
    assert not any("Example Framework" in t for _n, t in work)


def test_a_skills_section_after_certificates_is_still_collected():
    """The fourth case, and the only one that can observe the no-region bypass: the three
    others all keep `in_work` true. `CERTIFICATES` clears it, so an earlier revision put
    these lines in NO region at all while `parse_cv` still returned them as skills.

    Built as its OWN literal rather than through `_CV`/`tail`: the shared fixture above
    already carries its own SKILLS section (see `_CV` itself), and appending a second one
    past it via `tail` would leave that FIRST section active before CERTIFICATES ever
    clears anything -- a confound that defeats the isolation this case needs (measured:
    doing it that way collects THREE entries, not one, none of them wrong but none of
    them what this case is testing).
    """
    cv = ("PROFILE\nI did the work.\n\nWORK EXPERIENCE\n\nExample Alpha\n"
          "01/2020-01/2024 | LOCATION | Engineer\n- Ran the rebuild [AL1]\n\n"
          "CERTIFICATES\n- Example Practitioner\n\nSKILLS\n\n- Example Query\n")
    _p, _work, skills = V.section_spans(cv)
    assert [t.strip() for _n, t in skills] == ["- Example Query"]


# --- Shared test helpers for Tasks 4, 5 and 6 (#168) --------------------------------
#
# Tasks 4 (row 2, below), 5 (row 1) and 6 (digit handling -- span removal) all drive
# `validate()` through these five builders. Defined ONCE here and reused, never
# redefined, so three separately-implemented tasks cannot each grow their own CV/bundle
# shape and drift from one another the way `_CITE_RE`/tokeniser duplication would.


def _sources(*, body, skills, baseline):
    """One bundle entry -> BundleSources. Row 2 (`_in_source`, below) searches every
    source block regardless of which entry cites it, so this entry's id is incidental
    -- a test that needs ATTRIBUTION (row 1, Task 5) uses `_two_entry_sources` instead,
    which keeps AL1 and BE1 apart.

    `skills` is threaded into the entry's `fields` via `dict(Skills=skills)`, never a
    dict-LITERAL keyed the same way (deliberately not reproduced here: writing that
    exact colon-then-bare-parameter shape in this docstring would itself trip the
    collector described below, which scans comments too). With a runtime PARAMETER on
    the right of a colon, that literal spelling reads to
    tests/test_fixture_name_neutrality.py's `Skills:` collector (built for frontmatter
    and dict/kwarg literals alike, keyed on the colon) as if the bare Python parameter
    NAME were itself a declared skill value -- a false-positive fixture with nothing
    behind it. The `key=value` kwarg spelling has no colon after the key, so that
    collector never matches it; the dict `_skill_items` reads at runtime is identical
    either way. `tests/test_cv_bundle.py`'s own
    `test_every_token_of_a_skill_must_begin_with_a_letter` established this same
    workaround first, for the identical reason.
    """
    return bundle_sources(build_bundle(
        entries=[{"company": "Example Systems", "title": "Engineer", "metrics": "",
                  "body": body,
                  # `dict(Skills=skills)` is deliberate -- see this function's own
                  # docstring. Do not "tidy" this to a `Skills`-keyed dict LITERAL.
                  "fields": dict(Skills=skills), "best_for": "", "category": ""}],
        baseline=baseline, negatives=[], jd_keywords=[],
        prefix_map={"Example Systems": "ES"}))


def _two_entry_sources(*, al_skills, be_skills, baseline="Example Alpha."):
    """AL1 (Example Alpha) + BE1 (Example Beta) -> BundleSources, for row 1 (Task 5)
    and the digit tests (Task 6) that cite one entry against another.

    `prefix_map` is REQUIRED, not merely convenient: `cv/bundle.py`'s `_prefix` derives
    a fallback prefix from the company name's own first two letters when no override is
    given, and "Example Alpha" and "Example Beta" both derive "EX" -- ids would come out
    EX1/EX2, not the AL1/BE1 every citing test in this module (and the two later tasks
    that reuse this helper) names by hand.

    See `_sources` above for why `Skills` is threaded through as a keyword argument
    rather than a dict-literal colon.
    """
    return bundle_sources(build_bundle(
        entries=[
            {"company": "Example Alpha", "title": "Engineer", "metrics": "",
             # `dict(Skills=...)` is deliberate -- see `_sources`' docstring above.
             "body": "", "fields": dict(Skills=al_skills), "best_for": "", "category": ""},
            {"company": "Example Beta", "title": "Engineer", "metrics": "",
             "body": "", "fields": dict(Skills=be_skills), "best_for": "", "category": ""},
        ],
        baseline=baseline, negatives=[], jd_keywords=[],
        prefix_map={"Example Alpha": "AL", "Example Beta": "BE"}))


def _cv_with_skills(items):
    """A CV whose only content is a SKILLS region -- `section_spans`' third region, a
    contiguous bullet run. No PROFILE or WORK EXPERIENCE preamble: row 2 does not need
    a citable WORK bullet to fire, and with no `WORK EXPERIENCE` header the
    reverse-chronology check sees no start years and stays silent."""
    return "\n".join(["SKILLS"] + [f"- {item}" for item in items])


def _bullet(text):
    """A CV with one WORK bullet, `text` verbatim (citations included). One entry, so
    the reverse-chronology check sees a single start year and always passes clean."""
    return "\n".join(["PROFILE", "I did the work.", "", "WORK EXPERIENCE", "",
                      "Example Alpha", "01/2020-01/2024 | LOCATION | Engineer",
                      f"- {text}"])


def _cv(*, profile, bullet):
    """A CV with a caller-controlled PROFILE line and one caller-controlled WORK
    bullet -- Task 6's PROFILE-vs-bullet digit tests need the two set independently."""
    return "\n".join(["PROFILE", profile, "", "WORK EXPERIENCE", "",
                      "Example Alpha", "01/2020-01/2024 | LOCATION | Engineer",
                      f"- {bullet}"])


# --- Task 4: row 2 -- the emitted SKILLS section must come from the bundle ----------

def test_a_two_word_skill_is_matched_as_a_sequence_not_a_token():
    """Row 2's vocabulary is source TEXT, so whole-line set membership is undefined --
    no single token is "Widget Framework". Both rows use one subsequence primitive."""
    s = _sources(body="Ran a Widget Framework migration.", skills="",
                 baseline="Example Alpha.")
    assert s.source_tokens  # scope: the bundle actually carries blocks to search
    cv = _cv_with_skills(["Widget Framework"])
    assert V.section_spans(cv)[2]  # scope: the SKILLS region was actually collected
    assert V.validate(cv, s) == []
    assert any("UNSOURCED SKILL" in x
               for x in V.validate(_cv_with_skills(["Framework Widget"]), s))


def test_an_emitted_skill_absent_from_the_bundle_is_refused():
    s = _sources(body="Ran the rebuild.", skills="Example Query", baseline="Example Alpha.")
    assert s.source_tokens
    cv = _cv_with_skills(["Example Query", "Kubernetes"])
    assert V.section_spans(cv)[2]
    v = V.validate(cv, s)
    assert any("UNSOURCED SKILL" in x and "Kubernetes" in x for x in v)
    assert not any("Example Query" in x for x in v)


def test_row_2_licenses_a_skill_from_the_baseline_or_a_body():
    """SC4. `_RULES` and `_DERIVED_NEGATIVE_PROMPT` both license the BASELINE CV and
    verified entries, so a gate licensing only `Skills:` refuses what the prompt in the
    same run requires -- measured as `skipped-gate` on every lead."""
    s = _sources(body="Ran an Example Framework migration.", skills="",
                 baseline="Used Example Widget3 throughout.")
    assert s.source_tokens
    cv = _cv_with_skills(["Example Framework", "Example Widget3"])
    assert V.section_spans(cv)[2]
    assert V.validate(cv, s) == []


def test_row_2_fails_closed_when_no_entry_declares_skills():
    """SC5, and the most serious finding of the review: making row 2 CONDITIONAL on a
    non-empty vocabulary turned fail-closed into fail-OPEN. `section_spans` is pure over
    text, so its SKILLS region exists regardless -- a model-emitted section on an
    un-annotated vault would then be checked by NOTHING and render, where today it yields
    UNCITED BULLET. Only the _RULES request is conditional; this check always runs."""
    s = _sources(body="Ran the rebuild.", skills="", baseline="Example Alpha.")
    cv = _cv_with_skills(["Kubernetes"])
    assert V.section_spans(cv)[2]  # scope: the SKILLS region was actually collected
    v = V.validate(cv, s)
    assert any("UNSOURCED SKILL" in x for x in v)


def test_a_two_word_skill_spanning_a_block_seam_still_refuses_through_validate():
    """Task 4 review's inherited coverage gap: row 2 searches `source_tokens` PER BLOCK
    (tests/test_cv_bundle.py's own `test_source_tokens_are_per_block_so_a_two_word_
    skill_cannot_match_across_a_seam` pins that at the bundle-construction level), but
    nothing before this test drove `validate()` itself through the scenario -- the
    property was verified only by COMPOSITION (that unit test plus Task 4's per-block
    loop), which stops at the bundle rather than reaching the gate.

    AL1's body ends with the first word of a two-word skill and BE1's body begins with
    the second -- two SEPARATE blocks, so the seam between them is not itself a
    contiguous run anywhere in the user's prose."""
    b = build_bundle(
        entries=[
            {"company": "Example Alpha", "title": "Engineer", "metrics": "",
             "body": "Delivered the Example", "fields": dict(Skills=""),
             "best_for": "", "category": ""},
            {"company": "Example Beta", "title": "Engineer", "metrics": "",
             "body": "Framework rollout continued.", "fields": dict(Skills=""),
             "best_for": "", "category": ""},
        ],
        baseline="Example Alpha.", negatives=[], jd_keywords=[],
        prefix_map={"Example Alpha": "AL", "Example Beta": "BE"})
    s = bundle_sources(b)
    # Scope: assert the seam actually exists (the two tokens ARE adjacent across the
    # block boundary) before asserting on validate()'s output -- otherwise this test
    # would pass for the wrong reason (no seam to fail to bridge).
    assert s.source_tokens[0][-1] == "Example"
    assert s.source_tokens[1][0] == "Framework"
    cv = _cv_with_skills(["Example Framework"])
    assert V.section_spans(cv)[2]  # scope: the SKILLS region was actually collected
    v = V.validate(cv, s)
    assert any("UNSOURCED SKILL" in x and "Example Framework" in x for x in v)


# --- Task 5: row 1 -- a bullet's skill must belong to a cited entry -----------------

def test_a_skill_named_in_a_bullet_citing_the_wrong_entry_is_refused():
    s = _two_entry_sources(al_skills="Example Query", be_skills="Example Framework")
    v = V.validate(_bullet("Ran the Example Query work [BE1]"), s)
    assert any("MISATTRIBUTED SKILL" in x and "Example Query" in x for x in v)


def test_row_1_abstains_when_a_cited_entry_declares_no_skills():
    """SC5, measured in review: with the abstain condition bundle-wide instead of
    per-entry, a bullet citing an un-annotated entry and naming a skill present in THAT
    ENTRY'S OWN BODY was a hard violation -- the gate refusing a token from the cited
    entry's own source line.

    POSITIVE CONTROL (review round 1): the identical bullet and vocabulary, but with
    BE1's `Skills:` populated instead of blank, DOES report the violation -- pinning the
    silence below to the abstain condition rather than to the row never firing at all."""
    bullet = _bullet("Ran the Example Query work [BE1]")
    populated = _two_entry_sources(al_skills="Example Query", be_skills="Example Framework")
    assert any("MISATTRIBUTED SKILL" in x for x in V.validate(bullet, populated))
    s = _two_entry_sources(al_skills="Example Query", be_skills="")
    assert V.validate(bullet, s) == []


def test_row_1_abstains_on_a_blank_value_not_only_a_missing_key():
    """`_evidence_entries` materialises every declared field, so `Skills == ""` is the
    PRODUCTION shape and a key-omitting fixture proves nothing. A presence-keyed
    implementation passes that fixture while re-opening the over-fire above.

    POSITIVE CONTROL (review round 1): the identical bullet and vocabulary, but with
    BE1's `Skills:` populated instead of whitespace-only, DOES report the violation --
    pinning the silence below to the blank value rather than to the row never firing."""
    bullet = _bullet("Ran the Example Query work [BE1]")
    populated = _two_entry_sources(al_skills="Example Query", be_skills="Example Framework")
    assert any("MISATTRIBUTED SKILL" in x for x in V.validate(bullet, populated))
    s = _two_entry_sources(al_skills="Example Query", be_skills="   ")
    assert V.validate(bullet, s) == []


def test_row_1_is_case_sensitive_so_ordinary_english_never_collides():
    """SC9: row 1 scans free prose, where a short common-word skill name would otherwise
    collide with its ordinary sense. Every failure mode here is an UNDER-fire.

    `Widget` is the fixture BECAUSE it is an ordinary English noun as well as a plausible
    skill name -- that collision is the whole point of the row. It must stay invented: a
    real language or product name here would sit in the candidate's own declared-skills
    slot, which is the position a real skill set leaks from.

    POSITIVE CONTROL (review round 1): the identical bullet with the skill spelled in
    MATCHING case DOES report the violation -- pinning the silence below to case rather
    than to the skill being absent from the vocabulary or the bullet never being
    collected."""
    s = _two_entry_sources(al_skills="Widget", be_skills="Example Framework")
    assert any("MISATTRIBUTED SKILL" in x
               for x in V.validate(_bullet("Ran the Widget rework [BE1]"), s))
    assert V.validate(_bullet("Ran the widget rework [BE1]"), s) == []


# --- Task 6: digit handling -- span removal, decoupled from row 1 ------------------

def test_a_digit_bearing_skill_name_is_not_a_fabricated_metric():
    """Measured on main BEFORE this feature: `- Ran the migration on Example Widget3 … [AL1]`
    reports INVENTED METRIC ['3']. The only actionable answer is to delete a true skill
    name -- the LOCATION shape. Latent today; #168 makes it the main path."""
    s = _two_entry_sources(al_skills="Example Widget3", be_skills="Example Framework")
    assert V.validate(_bullet("Ran the Example Widget3 migration [AL1]"), s) == []


def test_a_declared_skills_digit_is_not_reported_invented_in_profile_prose():
    """Renamed and re-fixtured on review (was
    `test_profile_removal_uses_entry_skills_not_the_row_2_vocabulary`): the original
    fixture declared NO skills on either entry, so no span removal ever ran under
    EITHER candidate vocabulary, and the test proved nothing about which one PROFILE
    actually uses -- confirmed independently by a reviewer who tried to build a
    discriminating case and could not (see the paragraph below for why).

    This version at least exercises the removal it claims to pin: BE1 declares
    `Example Widget3` while the WORK bullet cites only AL1, so the PROFILE mention is
    licensed purely through the bundle-wide UNION of every entry's `Skills:` --
    consistent with `profile_permitted` already being bundle-wide -- and not through
    whichever entry happens to be cited elsewhere in the CV.

    THIS DOES NOT DISCRIMINATE `all_skills` from `sources.source_tokens`, and NO test
    can: `bundle_sources` derives `nums[entry]` from `_entry_block` (id line + body) via
    a direct `\\d+` regex, so every digit anywhere in any entry's body or the baseline is
    ALREADY independently `profile_permitted`, stripped or not. The only digits
    `source_tokens` carries beyond `nums` are skill-item digits -- and those are, by
    construction, always ALSO in `all_skills`, since a skill item can only exist inside
    some entry's `Skills:` field. So the two candidate vocabularies always produce the
    identical surviving digit set for PROFILE; this row is a regression pin on "a
    declared skill's digits are not reported invented in PROFILE prose", not a
    falsifier of the vocabulary choice. A test whose name promised that distinction
    would be worse than one that admits the limit.
    """
    s = _two_entry_sources(al_skills="", be_skills="Example Widget3")
    cv = _cv(profile="Deep experience with Example Widget3 overall.", bullet="Ran it [AL1]")
    assert not any("INVENTED PROFILE METRIC" in x for x in V.validate(cv, s))


def test_the_same_holds_in_profile_prose():
    """PROFILE has no citation to hang the per-entry rule on, so it uses the union of
    entry `Skills:` -- consistent with `profile_permitted` already being bundle-wide.

    FIXTURE NOTE, superseded and kept because the correction matters more than the
    note: the plan's literal fixture put the skill immediately before a sentence-final
    period ("Example Widget3."), which USED to produce a false INVENTED PROFILE METRIC.
    An earlier revision of this docstring deferred that as "a pre-existing property of
    the tokeniser this task reuses verbatim". That was FALSE on its facts -- `git show
    f6dac28d:sluice/cv/bundle.py` has no `_WORD_RE` and `f6dac28d:sluice/cv/validate.py`
    has no `_tokens`; BOTH landed on this branch, so the defect was this task's own to
    fix and the deferral was reasoning from a mis-attributed origin.

    It is fixed: `_WORD_RE` no longer folds a TRAILING dot into the token before it
    (internal and leading dots survive, so `Node.js`/`.NET` stay one token). This test
    keeps the space-separated spelling because it is testing the row-2-vocabulary
    distinction its name describes; the sentence-final spelling is pinned separately by
    `test_a_trailing_period_does_not_split_a_declared_skill_from_its_own_name` below.
    """
    s = _two_entry_sources(al_skills="Example Widget3", be_skills="")
    cv = _cv(profile="Deep experience with Example Widget3 overall.", bullet="Ran it [AL1]")
    assert not any("INVENTED PROFILE METRIC" in x for x in V.validate(cv, s))


def test_span_removal_does_not_depend_on_row_1_passing():
    """The review's sharpest finding: while removal was gated on row 1's verdict, three
    decisions combined into the harm it exists to prevent. Row 1 abstains on an
    un-annotated cited entry (SC5), and skill digits sit in NO numeric pool -- so a
    row-1 abstain became a hard INVENTED METRIC on a skill the user really declared.
    Removal is a numeric-gate concern, not an attribution verdict.

    Confirmed by the mutation witness (Step 5): moving the digit-removal vocabulary so
    it is computed ONLY inside row 1's `if all(...)` arm (i.e. gated on row 1 running at
    all, matching a plausible earlier revision) makes exactly this assertion fail, with
    the exact over-fire this docstring describes.
    """
    s = _two_entry_sources(al_skills="Example Widget3", be_skills="")
    assert not any("INVENTED" in x
                   for x in V.validate(_bullet("Ran Example Widget3 [AL1][BE1]"), s))


def test_span_removal_is_case_insensitive_unlike_row_1():
    """A DIFFERENT under-fire than the abstain case above: row 1 (SC9) is
    case-SENSITIVE by design, so `example widget3` does not case-match the declared
    `Example Widget3` and row 1 correctly stays silent about misattribution. Digit
    removal must not inherit that case-sensitivity -- it answers a different question
    (is this digit part of a real name) and is decided independently of row 1's
    verdict, so it must still strip the span case-insensitively and must not report the
    embedded `3` as an invented metric.

    Mislabelled on first review: this arm previously lived inside
    `test_span_removal_does_not_depend_on_row_1_passing`, but the mutation witness for
    THAT test (gating removal on row 1's `if all(...)` arm) does not make this
    assertion fail -- a single citation with a declared skill means row 1's `licensed`
    set and this row's own removal vocabulary coincide, so this arm witnesses
    case-insensitivity, not row-1 decoupling, and needed its own name.

    FIXTURE NOTE: an earlier draft of this fixture ("Ran widget3 [AL1]") dropped the
    "Example" token entirely rather than just lower-casing it, so the two-token needle
    `["Example", "Widget3"]` could never match a one-token haystack -- that fails
    independent of case-sensitivity, for the same reason a needle longer than its
    haystack window never matches. Both tokens are kept here, lower-cased, so the
    case-INSENSITIVE span match this row exists to prove is what is actually exercised.
    """
    s = _two_entry_sources(al_skills="Example Widget3", be_skills="Example Framework")
    assert not any("INVENTED" in x for x in V.validate(_bullet("Ran example widget3 [AL1]"), s))


def test_a_fabricated_number_beside_a_licensed_skill_is_still_caught():
    """The OVER-fire direction, which the guard list originally omitted. Removing a span
    must not become a hole."""
    s = _two_entry_sources(al_skills="Example Widget3", be_skills="")
    v = V.validate(_bullet("Ran Example Widget3 and cut cost by 92% [AL1]"), s)
    assert any("INVENTED METRIC" in x and "92" in x for x in v)


def test_a_substring_prefix_match_does_not_launder_a_fabricated_metric():
    """The substring-containment hole SC9 forbids by name (`"java" in "javascript"`),
    reproduced as a LIVE regression rather than argued from the code: mutating
    `_strip_skill_spans` back to a naive `re.sub(re.escape(skill), "", text)` leaves the
    ENTIRE suite green, including every other row in this file -- this is the one row
    that catches it, and it did not exist until a reviewer built the exploit by hand.

    The shape: `Skills: Example Widget3` licenses stripping the token `Widget3`. A
    substring `re.sub` also strikes `Widget3` out of the UNRELATED token `Widget30`
    (which merely shares a prefix), leaving the bare digit `0` behind. If that leftover
    `0` happens to be independently licensed -- here, by the entry's own BODY -- the
    genuinely fabricated `30` is never even extracted, let alone flagged: the mutant
    reports zero violations. The real (token-sequence) implementation never performs
    the strike-out at all, since `Widget30` and `Widget3` are different tokens, so `30`
    is extracted whole and correctly reported as invented.
    """
    s = _sources(body="Logged 0 regressions this quarter.", skills="Example Widget3",
                 baseline="Example Alpha.")
    v = V.validate(_bullet("Ran the Example Widget30 migration [ES1]"), s)
    assert any("INVENTED METRIC" in x and "30" in x for x in v)


# --- The trailing-period tokeniser defect (#168 review) -----------------------------
#
# `_WORD_RE` shipped as `[A-Za-z0-9#+.]+`, which folded a SENTENCE-FINAL period into the
# token before it. Only `.` did that, and it broke all three consumers of the ONE
# tokeniser at once -- span removal (row 1's digit handling), row 1's own prose scan, and
# row 2's containment search -- because the declared skill tokenises without the period
# and the emitted prose tokenises with it, so the sequence match never fires.
#
# Every row below is the SAME shape a real CV has: a skill name at the end of a sentence.
# `S3`, `p99`, `OAuth2` and `Log4j` all sit there in ordinary prose, and each of the three
# refusals below was answerable only by DELETING or CORRUPTING true content -- dropping a
# real digit out of a product name, or deleting a skill the bundle genuinely carries.


def test_a_trailing_period_does_not_read_a_skill_digit_as_a_fabricated_metric():
    """Row 1's digit handling. Measured before the fix:
    `INVENTED METRIC ['3'] not in ['AL1']` -- the only actionable answer to which is to
    ship `Examplestore` for a product really called `Examplestore3`."""
    s = _two_entry_sources(al_skills="Examplestore3", be_skills="")
    v = V.validate(_cv(profile="I did the work.",
                       bullet="Migrated to Examplestore3. [AL1]"), s)
    assert not any("INVENTED METRIC" in x for x in v), v


def test_a_trailing_period_does_not_split_a_declared_skill_from_its_own_name():
    """The PROFILE arm of the same defect. Measured before the fix:
    `INVENTED PROFILE METRIC 3 not in bundle`."""
    s = _two_entry_sources(al_skills="Examplestore3", be_skills="")
    v = V.validate(_cv(profile="Specialist in Examplestore3.", bullet="Ran it [AL1]"), s)
    assert not any("INVENTED PROFILE METRIC" in x for x in v), v


def test_a_skill_ending_a_sentence_in_a_body_is_not_reported_unsourced():
    """Row 2. The bundle DOES contain `Example Widget` -- it is the last thing in the
    entry's own body sentence. Measured before the fix: `UNSOURCED SKILL 'Example
    Widget': not in the bundle`, which is simply false, and answerable only by deleting
    a true skill."""
    s = _sources(body="The whole estate ran on Example Widget.", skills="",
                 baseline="Example Alpha.")
    assert s.source_tokens                      # scope: there are blocks to search
    cv = _cv_with_skills(["Example Widget"])
    assert V.section_spans(cv)[2]               # scope: the region was collected
    assert V.validate(cv, s) == []


def test_an_internal_dot_still_holds_a_name_together():
    """The half the fix must NOT break. `Node.js` is ONE token: splitting it would make a
    two-token needle (`Node`, `js`) that no `Skills:` value could match, turning every
    dotted technology name into an UNSOURCED SKILL. Asserted through the gate, not
    through `_tokens`, so it fails if either the build or the search side drifts."""
    s = _sources(body="Ran a Node.js migration.", skills="", baseline="Example Alpha.")
    cv = _cv_with_skills(["Node.js"])
    assert V.section_spans(cv)[2]
    assert V.validate(cv, s) == []
    # ...and the name is still a NAME, not a prefix: `Node` alone is not in the bundle.
    assert any("UNSOURCED SKILL" in x for x in V.validate(_cv_with_skills(["Node"]), s))


def test_a_bare_period_run_is_not_a_token_at_all():
    """The old pattern matched `...` as a token in its own right, so an ellipsis in a
    body was a licensed 'word' row 2 could match a skill against. Nothing shipped
    depended on it, and removing it is part of the same one-character change -- pinned so
    a later 'simplification' back to a character class cannot restore it silently."""
    assert V._tokens("a ... b") == ["a", "b"]
    assert V._tokens("trailing dots... here") == ["trailing", "dots", "here"]
