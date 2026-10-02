"""cv/bundle.py::mention_vocab (#194): the vocabulary the unbundled-term check RECOGNISES.

Spec: docs/superpowers/specs/2026-10-02-unbundled-term-design.md §3.3. One row per
source, never one row for all -- a single row over a bundle carrying every source
cannot tell which source suppressed the term.
"""
from sluice.cv.bundle import _WORD_RE, build_bundle, bundle_sources, mention_vocab
from sluice.cv.validate import validate


def _bundle(*, baseline="", entries=(), negatives=(), skills=()):
    return build_bundle(list(entries), baseline, list(negatives), [], {}, skills=list(skills))


def _entry(**kw):
    e = {"title": "", "company": "", "metrics": "", "body": "", "fields": {}}
    e.update(kw)
    return e


def test_the_baseline_is_in_the_vocabulary():
    assert "examplequery" in mention_vocab(_bundle(baseline="Ran ExampleQuery daily."))


def test_an_entry_heading_is_in_the_vocabulary():
    v = mention_vocab(_bundle(entries=[_entry(title="Built Examplebus", company="Example Co")]))
    assert "examplebus" in v


def test_an_entry_body_is_in_the_vocabulary():
    assert "examplestore" in mention_vocab(_bundle(entries=[_entry(body="Moved to Examplestore.")]))


def test_an_entry_skills_field_is_in_the_vocabulary():
    v = mention_vocab(_bundle(entries=[_entry(fields={"Skills": "Examplemesh"})]))
    assert "examplemesh" in v


def test_a_skills_inventory_note_is_in_the_vocabulary():
    """IN, though no HARD row licenses it (spec §3.3): this check asks whether the term was
    INVENTED, and a declared skill was not."""
    v = mention_vocab(_bundle(skills=[{"title": "Examplelang", "fields": {}, "body": ""}]))
    assert "examplelang" in v


def test_a_presentation_header_word_is_not_in_the_vocabulary():
    """`=== VERIFIED EXPERIENCE ENTRIES ... ===` is prompt scaffolding, not the user's words.
    `verified` appears in no source below, so its presence could only come from a header."""
    v = mention_vocab(_bundle(baseline="Ran things.", entries=[_entry(body="Did things.")]))
    assert "verified" not in v
    assert "baseline" not in v


def test_a_negative_term_is_subtracted_even_when_an_inventory_note_carries_it():
    """Subtracted by TERM, not merely kept out as a source (spec §3.3): a configured
    'never claim X', with X written name-shaped, must report X however else the bundle
    came to mention it."""
    b = _bundle(skills=[{"title": "Examplelang", "fields": {}, "body": ""}],
                negatives=["never claim Examplelang"])
    assert "examplelang" not in mention_vocab(b)


def test_a_lowercase_negative_term_stays_recognised_when_another_source_carries_it():
    """The LIMIT of the subtraction, pinned as documented behaviour (spec §3.3, §7): a
    negative subtracts only its own CANDIDATE tokens, and a term written all lowercase in
    the negative is not one, so "never claim examplelang" leaves an inventory note's
    Examplelang recognised. Only a name-shaped spelling in the negative suppresses it."""
    b = _bundle(skills=[{"title": "Examplelang", "fields": {}, "body": ""}],
                negatives=["never claim examplelang"])
    assert "examplelang" in mention_vocab(b)


def test_a_negative_subtracts_only_its_own_candidate_tokens():
    """Subtracted by CANDIDATE, not by every token (spec §3.3): a free-text negative's
    ordinary words must stay recognised, or a capitalised `Platform` elsewhere in the CV
    would be reported on every lead. Its name-shaped term is still subtracted."""
    b = _bundle(baseline="Led the platform team; used Examplequery.",
                negatives=["Do not overstate Examplequery design or platform leadership"])
    v = mention_vocab(b)
    assert "platform" in v
    assert "examplequery" not in v


def test_every_entry_skill_token_is_in_the_vocabulary():
    """The SUPERSET guard behind spec §3.4's disjointness: row 1 (MISATTRIBUTED SKILL)
    reports only terms in this set's skills subset, the unbundled-term check only terms
    outside the whole set, so no term can be reported by both."""
    entries = [_entry(company="Example A", fields={"Skills": "Example Widget, Examplemesh"}),
               _entry(company="Example B", fields={"Skills": "Examplebus"})]
    b = _bundle(entries=entries)
    v = mention_vocab(b)
    skill_tokens = {t.casefold() for es in bundle_sources(b).entries.values()
                    for s in es.skills for t in _WORD_RE.findall(s)}
    assert skill_tokens, "the sweep enumerated no skill tokens at all"
    assert skill_tokens <= v


def test_the_vocabulary_cannot_widen_the_hard_gate():
    """SEPARATION (spec §3.3, review INV-1): mention_vocab is a STYLE-only pool and
    deliberately carries an inventory-only skill. Row 2 must still REFUSE that skill on a
    SKILLS line -- asserted on the discriminating message, not on emptiness, so a gate that
    started reading this pool would go red here."""
    b = _bundle(baseline="Ran things.",
                skills=[{"title": "Examplelang", "fields": {}, "body": ""}])
    assert "examplelang" in mention_vocab(b), "premise: the pool does carry the skill"
    cv = "\n".join(["Jane Roe", "PROFILE", "Ran things.", "WORK EXPERIENCE",
                    "SKILLS", "- Examplelang"])
    assert any(v.startswith("UNSOURCED SKILL 'Examplelang'")
               for v in validate(cv, bundle_sources(b)))


def test_a_non_ascii_name_tokenises_identically_on_both_sides():
    """Review Focus 5: `_WORD_RE` is ASCII-only, so `Exämple` fragments. The vocabulary
    must hold the same fragments the check will look up."""
    v = mention_vocab(_bundle(baseline="Worked at Exämple."))
    assert {"ex", "mple"} <= v


def test_a_negative_is_not_left_behind_as_a_source_of_its_own():
    # The ONLY mention of the token is a lowercase negative, so the subtraction has nothing
    # to cancel: the token is absent only if negatives are never ADDED as a source.
    b = _bundle(baseline="Delivered reports.", negatives=["never claim examplelang"])
    assert "examplelang" not in mention_vocab(b)
