"""cv/bundle.py::term_vocabulary (#194): the vocabulary the unbundled-term check RECOGNISES.

Spec: docs/superpowers/specs/2026-10-02-unbundled-term-design.md §3.3, as rebuilt by
#364/#365/#368 spec §8: the entries, their tools, the Skills Inventory framing and the CV
Layout, and nothing else -- no baseline, and no negatives. One row per source, never one row
for all -- a single row over a bundle carrying every source cannot tell which source
suppressed the term.
"""
from sluice.core.protocols import CvLayout, LayoutRole
from sluice.core.tokens import WORD_RE as _WORD_RE
from sluice.core.tokens import tool_items
from sluice.cv.bundle import build_bundle, term_vocabulary

# Synthetic and word-free of every term the rows below look up, so a hit can only come from
# the source under test.
_LAYOUT = CvLayout(roles=(LayoutRole("Example Co", "01/2020", "present"),))


def _vocab(*, entries=(), negatives=(), skills=()):
    b = build_bundle(list(entries), list(negatives), [], {}, skills=list(skills))
    return term_vocabulary(b, _LAYOUT)


def _entry(**kw):
    e = {"title": "", "company": "", "metrics": "", "body": "", "fields": {}}
    e.update(kw)
    return e


def test_an_entry_heading_is_in_the_vocabulary():
    v = _vocab(entries=[_entry(title="SYNTHETIC Built Examplebus", company="Example Co")])
    assert "examplebus" in v


def test_an_entry_body_is_in_the_vocabulary():
    assert "examplestore" in _vocab(entries=[_entry(body="Moved to Examplestore.")])


def test_an_entry_tools_field_is_in_the_vocabulary():
    assert "examplemesh" in _vocab(entries=[_entry(fields=dict(Tools="Examplemesh"))])


def test_a_skills_inventory_note_is_in_the_vocabulary():
    """IN, though no HARD row licenses it (spec §3.3): this check asks whether the term was
    INVENTED, and a declared skill was not."""
    assert "examplelang" in _vocab(skills=[{"title": "Examplelang", "fields": {}, "body": ""}])


def test_a_presentation_header_word_is_not_in_the_vocabulary():
    """`=== VERIFIED EXPERIENCE ENTRIES ... ===` is prompt scaffolding, not the user's words.
    `verified` appears in no source below, so its presence could only come from a header."""
    v = _vocab(entries=[_entry(body="Did things.")])
    assert "verified" not in v
    assert "baseline" not in v


def test_every_entry_tool_token_is_in_the_vocabulary():
    """The SUPERSET guard behind #194 spec §3.4's disjointness: MISATTRIBUTED TOOL reports only
    terms in this set's tools subset, the unbundled-term check only terms outside the whole
    set, so no term can be reported by both."""
    entries = [_entry(company="Example A", fields=dict(Tools="Example Widget, Examplemesh")),
               _entry(company="Example B", fields=dict(Tools="Examplebus"))]
    v = _vocab(entries=entries)
    tool_tokens = {t.casefold() for e in entries for item in tool_items(e)
                   for t in _WORD_RE.findall(item)}
    assert tool_tokens, "the sweep enumerated no tool tokens at all"
    assert tool_tokens <= v


def test_a_non_ascii_name_tokenises_identically_on_both_sides():
    """Review Focus 5: `_WORD_RE` is ASCII-only, so `Exämple` fragments. The vocabulary
    must hold the same fragments the check will look up."""
    assert {"ex", "mple"} <= _vocab(entries=[_entry(body="Worked at Exämple.")])


def test_a_negative_is_not_left_behind_as_a_source_of_its_own():
    # The ONLY mention of the token is a lowercase negative. It is absent because the
    # vocabulary never reads negatives at all (#368), not because a subtraction cancelled it.
    assert "examplelang" not in _vocab(entries=[_entry(body="Delivered reports.")],
                                       negatives=["never claim examplelang"])
