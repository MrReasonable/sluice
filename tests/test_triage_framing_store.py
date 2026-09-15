"""Store-facing rows for the triage framing keys (#329): what a note carries, and what a triage
write may and may not do to a value a human typed into it."""
import os

import pytest
import yaml

from sluice.core.leads import Lead
from sluice.core.vault import Vault
from sluice.triage.apply import apply_verdict
from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS


def _frontmatter_lines(path):
    text = open(path, encoding="utf-8").read()
    return text.split("---\n")[1].splitlines()


def test_a_new_note_carries_a_blank_triage_concerns_key(tmp_path):
    # Written blank at creation, directly after `culture_flags`, so a note's schema does not
    # depend on whether triage has run yet.
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title="Analyst", company="Example Foundry",
                  url="https://example.invalid/1"))
    lines = _frontmatter_lines(v.read_leads()[0].ref)
    assert 'triage_concerns: ""' in lines
    assert lines.index('triage_concerns: ""') == lines.index('culture_flags: ""') + 1


def _seed_note(tmp_path, fm_lines, name="Example Foundry - Analyst.md"):
    v = Vault(str(tmp_path))
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    path = os.path.join(leads, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\n" + "\n".join(fm_lines) + "\n---\n# body\n")
    return v, path


_VERDICT = {"verdict": "shortlist", "relevance_score": 81,
            "concerns": list(FRAMING_CONCERNS), "culture_flags": list(FRAMING_FLAGS)}

# Obsidian writes a List property as the first shape; the others are what a person typing YAML by
# hand reaches for. Item text is synthetic and colon-free, so no item line reads as a key.
_MULTI_LINE_SHAPES = {
    "list-indented": ["{key}:", "  - KEPT-ONE", "  - KEPT-TWO"],
    "list-same-indent": ["{key}:", "- KEPT-ONE", "- KEPT-TWO"],
    # A trailing comment is not a value: YAML still reads the items below as the key's list.
    "list-same-indent-commented": ["{key}:  # typed by hand", "- KEPT-ONE", "- KEPT-TWO"],
    "block-scalar": ["{key}: |", "  KEPT-ONE", "  KEPT-TWO"],
    # A column-0 comment line between the key and its items is not the key's value either.
    "list-after-comment-line": ["{key}:", "# typed by hand", "- KEPT-ONE", "- KEPT-TWO"],
    # #329: a block scalar whose body lines ALL start with `#` (Obsidian's own tag syntax) --
    # a deeper `#` line is content, not a skippable comment.
    "block-scalar-hash-body": ["{key}: |", "  #tag-one", "  #tag-two"],
    # A comment line INDENTED deeper than the key, ahead of the list's own items, is also content
    # by the same rule -- it must not fold into the column-0 case above.
    "list-after-indented-comment": ["{key}:", "  # typed by hand", "  - KEPT-ONE"],
    # A block-scalar header with a trailing comment on the key's own line, then a `#` body line
    # indented deeper than the key: the deeper line is what keeps the value unwritten.
    "block-scalar-header-trailing-comment-hash-body": ["{key}: >- # typed by hand", "  #tag-one"],
    # A nested mapping under the key, not a list -- the generic "indented deeper" rule alone.
    "nested-mapping": ["{key}:", "  nested: KEPT-NESTED"],
    # A block-scalar header carrying a tag or an anchor, and a quoted scalar continued on an
    # indented line that reads like a comment. `core/vault.py::_holds_multiline_value` does not
    # parse the key's own line, so it cannot tell these from a one-line value with an indented
    # comment under it, and leaves every such value unwritten rather than overwrite one of these.
    "block-scalar-tagged-hash-body": ["{key}: !!str |", "  #tag-one"],
    "block-scalar-anchored-hash-body": ["{key}: &a >-", "  #tag-one"],
    "double-quoted-over-lines": ['{key}: "first', '  # second"'],
    "single-quoted-over-lines": ["{key}: 'first", "  # second'"],
}


@pytest.mark.parametrize("key", ["triage_concerns", "culture_flags"])
@pytest.mark.parametrize("shape", sorted(_MULTI_LINE_SHAPES))
def test_a_hand_typed_multi_line_value_survives_a_triage_write(tmp_path, caplog, key, shape):
    block = [line.format(key=key) for line in _MULTI_LINE_SHAPES[shape]]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", *block, 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    # The whole block, byte for byte: an orphaned item line under a rewritten key is exactly the
    # corruption this guards, and it survives any check that reads the note back through sluice.
    assert lines[start:start + len(block)] == block
    # ...and what that protects: the note is still valid YAML, which is how Obsidian reads it.
    assert isinstance(yaml.safe_load("\n".join(lines)), dict)
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any(key in m for m in said), said
    assert not any("KEPT-ONE" in m for m in said)


def test_a_blank_key_followed_by_another_key_is_written_normally(tmp_path):
    v, _ = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                 "status: new", "score: 0", "triage_concerns:",
                                 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    assert v.read_leads()[0].fm["triage_concerns"] == "; ".join(FRAMING_CONCERNS)


def test_a_blank_key_followed_by_a_column0_comment_then_another_key_is_written_normally(tmp_path):
    # The column-0 comment is skipped, as in the `list-after-comment-line` shape; what
    # follows it is another key at the same indentation, so the value is single-line and
    # the write lands.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", "triage_concerns:",
                                    "# typed by hand", 'next_key: "x"', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    assert v.read_leads()[0].fm["triage_concerns"] == "; ".join(FRAMING_CONCERNS)
    lines = _frontmatter_lines(path)
    assert "# typed by hand" in lines
    assert 'next_key: "x"' in lines


def _left_unwritten(tmp_path, caplog, key, block, before_block, after_block):
    """Seed `block` between `before_block` and `after_block`, apply a verdict, and assert the
    block and what follows it are still there byte for byte, `key` reads back as it did, the
    verdict's other fields landed, and a `sluice.core.vault` warning names `key`."""
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", *before_block, *block,
                                    *after_block])
    note = v.read_leads({"new"})[0]
    before = note.fm[key]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    assert lines[start:start + len(block) + len(after_block)] == [*block, *after_block]
    after = v.read_leads()[0]
    assert after.fm[key] == before
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any(key in m for m in said), said


def test_a_one_line_value_followed_by_an_indented_comment_is_left_unwritten(tmp_path, caplog):
    # The trade-off `core/vault.py::_holds_multiline_value` takes (#329): any following line
    # indented deeper than the key counts, a comment included, so this one-line value is left
    # unwritten and logged. The same lines can open a value the helper cannot fully parse -- the
    # quoted scalar continued on an indented line in `_MULTI_LINE_SHAPES` looks exactly like this
    # -- and writing over one of those would corrupt it with no warning.
    _left_unwritten(tmp_path, caplog, "triage_concerns",
                    ['triage_concerns: "OLD-VALUE"', "  # typed by hand"],
                    ['relevance_notes: ""'], ['next_key: "x"'])


def test_a_blank_key_followed_by_an_indented_comment_is_left_unwritten(tmp_path, caplog):
    # The same trade-off for a blank key: the indented comment counts as a deeper line, so the
    # key is left as it is and logged. A column-0 comment in the same place is skipped instead,
    # and the write lands (the column-0 row above).
    _left_unwritten(tmp_path, caplog, "triage_concerns",
                    ["triage_concerns:", "  # typed by hand"],
                    ['relevance_notes: ""'], ['next_key: "x"'])


def test_a_relevance_notes_value_followed_by_an_indented_comment_is_left_unappended(
        tmp_path, caplog):
    # The same trade-off on the append path: the indented comment counts as a deeper line, so
    # the append is left undone and logged, and the one-line note reads back as it was.
    _left_unwritten(tmp_path, caplog, "relevance_notes",
                    ['relevance_notes: "OLD-NOTE"', "  # typed by hand"],
                    ['culture_flags: ""', 'triage_concerns: ""'], ['next_key: "x"'])


def test_a_nested_concerns_line_under_another_property_is_left_alone(tmp_path):
    # Why the key is `triage_`-prefixed: `_set_fm` matches a key at ANY indentation, so a bare
    # `concerns` write would land on this nested line.
    nested = ["hand_notes:", "  concerns: KEPT-NESTED", "  owner: KEPT-OWNER"]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", *nested, 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    lines = _frontmatter_lines(path)
    start = lines.index("hand_notes:")
    assert lines[start:start + len(nested)] == nested


def test_a_nested_key_before_the_real_one_is_matched_by_first_occurrence(tmp_path):
    # #329: `_holds_multiline_value` must match the FIRST occurrence of the key -- the same
    # line `_set_fm` would replace -- not the last. A nested `culture_flags:` inside
    # `hand_notes`, ahead of the real top-level `culture_flags: ""`, is what a write would
    # actually land on; reading the LAST occurrence would see the real key's single-line value
    # instead, judge the key safe to write, and corrupt `hand_notes` when the write landed on
    # the nested line anyway.
    hand_notes = ["hand_notes:", "  culture_flags:", "    - KEPT-ONE"]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", *hand_notes,
                                    'culture_flags: ""', 'triage_concerns: ""',
                                    'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    lines = _frontmatter_lines(path)
    start = lines.index(hand_notes[0])
    assert lines[start:start + len(hand_notes)] == hand_notes


@pytest.mark.parametrize("typed,read", [
    ('triage_concerns: "KEPT-ONE; KEPT-TWO"', "KEPT-ONE; KEPT-TWO"),
    ("triage_concerns: [KEPT-ONE, KEPT-TWO]", "[KEPT-ONE, KEPT-TWO]"),
])
def test_how_a_hand_typed_single_line_value_reads_back(tmp_path, typed, read):
    v, _ = _seed_note(tmp_path, ['company: "Example Foundry"', "status: shortlist", typed])
    assert v.read_leads()[0].fm["triage_concerns"] == read


def test_a_hand_typed_block_list_reads_back_blank(tmp_path):
    v, _ = _seed_note(tmp_path, ['company: "Example Foundry"', "status: shortlist",
                                 "triage_concerns:", "  - KEPT-ONE"])
    assert v.read_leads()[0].fm["triage_concerns"] == ""


def test_a_multi_line_key_is_written_when_it_is_not_preserved(tmp_path):
    # The control for the rows above: without the keyword the same single-line write lands on
    # the key's own line, so those rows are red only for the guard.
    v, _ = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                 "status: new", "triage_concerns:", "  - KEPT-ONE",
                                 "  - KEPT-TWO"])
    v.update_fields(v.read_leads()[0].ref, {"triage_concerns": '"written"'})
    assert v.read_leads()[0].fm["triage_concerns"] == "written"


def test_a_preserved_key_is_decided_before_any_field_is_written(tmp_path):
    # `_set_fm` matches a key at ANY indentation, so the verdict's `score` write lands on the
    # nested `score:` child first and moves it to column 0. A check made after that write sees
    # `culture_flags:` followed by a key at its own indentation, reads it as single-line, and
    # overwrites it. The nested child is lost either way (that is `_set_fm`'s own hazard, and
    # why this row does not assert valid YAML); what the guard owes is the key it was named for.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "culture_flags:", "  score: KEPT-NESTED",
                                    "score: 0", 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    assert "culture_flags:" in _frontmatter_lines(path)


def test_the_append_guard_is_decided_before_any_field_is_written(tmp_path):
    # #329: the append guard is its own decision, made against the same fresh
    # `inner` as the `preserve_block_values` check above and for the identical reason -- the
    # verdict's `score` write lands on the nested `score:` child first and moves it to column
    # 0, so a decision made AFTER the field loop would see `relevance_notes:` followed by a
    # key at its own indentation and append over it.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", 'culture_flags: ""', 'triage_concerns: ""',
                                    "relevance_notes:", "  score: KEPT-NESTED", "score: 0"])
    note = v.read_leads({"new"})[0]
    apply_verdict(v, note, dict(_VERDICT), {})
    assert "relevance_notes:" in _frontmatter_lines(path)


def test_a_hand_typed_relevance_notes_block_survives_an_append(tmp_path, caplog):
    # #329: the append is its own write path, not a `fields` key, so
    # `preserve_block_values` does not cover it -- but writing a single-line `relevance_notes`
    # over a hand-typed block corrupts it exactly the way a `fields` write would, and the
    # append must abstain the same way.
    block = ["relevance_notes: |", "  HAND-ONE", "  HAND-TWO"]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", 'culture_flags: ""',
                                    'triage_concerns: ""', *block])
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    # The whole block, byte for byte, exactly as the multi-line framing rows above pin it.
    assert lines[start:start + len(block)] == block
    assert isinstance(yaml.safe_load("\n".join(lines)), dict)
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("relevance_notes" in m for m in said), said
    assert not any("HAND-ONE" in m for m in said)


def test_a_hand_typed_relevance_notes_hashtag_block_survives_an_append(tmp_path, caplog):
    # #329: a block scalar whose body lines are ALL Obsidian tags (`#tag-one`, `#tag-two`) is
    # still a value spread over several lines; the append must abstain the same way it does for
    # the plain-text block above.
    block = ["relevance_notes: |", "  #tag-one", "  #tag-two"]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", 'culture_flags: ""',
                                    'triage_concerns: ""', *block])
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert isinstance(yaml.safe_load("\n".join(lines)), dict)
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("relevance_notes" in m for m in said), said
    assert not any("tag-one" in m for m in said)


@pytest.mark.parametrize("shape", ["block-scalar-tagged-hash-body",
                                   "block-scalar-anchored-hash-body",
                                   "double-quoted-over-lines", "single-quoted-over-lines"])
def test_a_relevance_notes_value_the_helper_does_not_parse_survives_an_append(
        tmp_path, caplog, shape):
    # The append path for the `_MULTI_LINE_SHAPES` entries whose own line
    # `core/vault.py::_holds_multiline_value` does not parse: a tagged or anchored block scalar
    # and a quoted scalar continued on an indented line. Each is left undone and logged, never
    # written over.
    block = [line.format(key="relevance_notes") for line in _MULTI_LINE_SHAPES[shape]]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0", 'culture_flags: ""',
                                    'triage_concerns: ""', *block])
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert isinstance(yaml.safe_load("\n".join(lines)), dict)
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("relevance_notes" in m for m in said), said


def test_an_ordinary_relevance_notes_still_gets_the_append(tmp_path):
    # The control for the row above: a single-line `relevance_notes` is not a value spread
    # over several lines, so the guard does not fire and the append lands as it always has.
    v, _ = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                 "status: new", "score: 0", 'culture_flags: ""',
                                 'triage_concerns: ""', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    assert v.read_leads()[0].fm["relevance_notes"] != ""


_NOTE = "[probe] SYNTHETIC-NOTE"


def _append(path, v):
    v.update_fields(v.read_leads()[0].ref, {"status": "research"},
                    append_note=_NOTE, note_tag="[probe]")
    lines = _frontmatter_lines(path)
    return lines, yaml.safe_load("\n".join(lines))


def test_an_append_onto_a_blank_relevance_notes_over_a_column0_comment_writes_only_the_note(
        tmp_path):
    # #329: the append reads an existing note from the key's own line. A blank
    # `relevance_notes:` holds none, so the comment line under it must not be read as one and
    # merged into the appended text.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "relevance_notes:", "# typed by hand",
                                    'next_key: "x"'])
    lines, parsed = _append(path, v)
    assert parsed["relevance_notes"] == _NOTE
    assert "# typed by hand" in lines
    assert 'next_key: "x"' in lines and parsed["next_key"] == "x"


def test_an_append_onto_a_blank_relevance_notes_over_another_key_writes_the_note(
        tmp_path, caplog):
    # #329: the same read with another key directly under the blank one. Reading that key's text
    # as the existing note made the merged text unsafe for frontmatter, so the note was dropped
    # under a warning that blamed the note text rather than the read.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "relevance_notes:", 'next_key: "x"'])
    with caplog.at_level("WARNING"):
        lines, parsed = _append(path, v)
    assert parsed["relevance_notes"] == _NOTE
    assert 'next_key: "x"' in lines and parsed["next_key"] == "x"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert not any("unsafe" in m for m in said), said


def test_an_append_onto_a_one_line_relevance_notes_follows_the_earlier_note(tmp_path):
    # The control for the rows above: a note already on the key's own line is still read, and
    # the new note is appended after it.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", 'relevance_notes: "earlier"',
                                    'next_key: "x"'])
    _, parsed = _append(path, v)
    assert parsed["relevance_notes"] == "earlier " + _NOTE
