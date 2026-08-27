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
