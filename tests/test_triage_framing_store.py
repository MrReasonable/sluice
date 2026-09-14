"""Store-facing rows for the triage framing keys (#329): what a note carries, and what a triage
write may and may not do to a value a human typed into it."""
import os

import pytest
import yaml

from sluice.core.leads import Lead
from sluice.core.vault import Vault, _holds_multiline_value
from sluice.triage.apply import apply_verdict
from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS


def _frontmatter_lines(path):
    text = open(path, encoding="utf-8").read()
    return text.split("---\n")[1].splitlines()


def _frontmatter_as_held(path):
    """The frontmatter text exactly as the file holds it between its delimiters, final newline
    included, which is what decides the value of a block scalar at its end."""
    return open(path, encoding="utf-8").read().split("---\n")[1]


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
    # recognise a block-scalar header, so it cannot tell the tagged and anchored shapes from a
    # one-line value with an indented comment under it, and leaves every such value unwritten
    # rather than overwrite one of them. The quoted shapes are also caught on the key's own line,
    # which leaves the quote open.
    "block-scalar-tagged-hash-body": ["{key}: !!str |", "  #tag-one"],
    "block-scalar-anchored-hash-body": ["{key}: &a >-", "  #tag-one"],
    "double-quoted-over-lines": ['{key}: "first', '  # second"'],
    "single-quoted-over-lines": ["{key}: 'first", "  # second'"],
    # A quoted scalar or a flow collection whose own line leaves it open, continued on an
    # unindented line.
    # No line below it is indented deeper than the key, so only the key's own line shows the
    # value is not finished; a YAML reader as lenient as PyYAML accepts each of these.
    "double-quoted-col0": ['{key}: "KEPT-ONE', 'KEPT-TWO"'],
    "single-quoted-col0": ["{key}: 'KEPT-ONE", "KEPT-TWO'"],
    "flow-sequence-col0": ["{key}: [KEPT-ONE,", "KEPT-TWO]"],
    "flow-mapping-col0": ["{key}: {{a: KEPT-ONE,", "b: KEPT-TWO}}"],
    # The same, for a quoted scalar whose own line ends in an escaped quote: the quote is still
    # open only because `\"` and `''` are escapes rather than closing quotes.
    "double-quoted-escaped-quote-col0": ['{key}: "KEPT-ONE\\"', 'KEPT-TWO"'],
    "single-quoted-doubled-quote-col0": ["{key}: 'KEPT-ONE''", "KEPT-TWO'"],
    # The same, behind a leading node property (a tag or an anchor): the quote or bracket that
    # leaves the value open comes after it.
    "tagged-double-quoted-col0": ['{key}: !!str "KEPT-ONE', 'KEPT-TWO"'],
    "anchored-double-quoted-col0": ['{key}: &x "KEPT-ONE', 'KEPT-TWO"'],
    "anchored-flow-sequence-col0": ["{key}: &x [KEPT-ONE,", "KEPT-TWO]"],
    "tagged-flow-sequence-col0": ["{key}: !!seq [KEPT-ONE,", "KEPT-TWO]"],
    # YAML's non-specific tag is a bare `!` with no name after it; the opener still follows it.
    "non-specific-tag-double-quoted-col0": ['{key}: ! "KEPT-ONE', 'KEPT-TWO"'],
    "non-specific-tag-flow-sequence-col0": ["{key}: ! [KEPT-ONE,", "KEPT-TWO]"],
}

# One row per member of the set `core/vault.py::_inline_value_left_open` measured against PyYAML:
# what a `#` inside an open flow collection may follow and still start a comment. Each first line
# closes everything it opens unless that `#` starts a comment, so only the member under test can
# keep the value unwritten; the unindented line below then closes what the comment left open.
_FLOW_COMMENT_SHAPES = {
    "open-bracket": ["{key}: [#c]", "KEPT-TWO]"],
    "close-bracket": ["{key}: [[a]#c]", ",KEPT-TWO]"],
    "open-brace": ["{key}: {{#c}}", "b: KEPT-TWO}}"],
    "close-brace": ["{key}: [{{a: b}}#c]", ",KEPT-TWO]"],
    "comma": ["{key}: [a,#c]", "KEPT-TWO]"],
    "question-mark": ["{key}: [?#c]", "KEPT-TWO]"],
    "closing-quote": ['{key}: ["a"#c]', ",KEPT-TWO]"],
    "closing-single-quote": ["{key}: ['a'#c]", ",KEPT-TWO]"],
    "colon-after-closing-quote": ['{key}: ["a":#c]', "KEPT-TWO]"],
    "colon-after-close-bracket": ["{key}: [[a]:#c]", "KEPT-TWO]"],
    "colon-after-close-brace": ["{key}: [{{a: b}}:#c]", "KEPT-TWO]"],
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
    # One warning per key left unwritten: a key the scan already held is not asked of PyYAML again,
    # which would log it a second time.
    assert len([m for m in said if key in m]) == 1, said
    assert not any("KEPT-ONE" in m for m in said)


@pytest.mark.parametrize("key", ["triage_concerns", "culture_flags"])
@pytest.mark.parametrize("member", sorted(_FLOW_COMMENT_SHAPES))
def test_a_hash_comment_after_a_measured_flow_member_survives_a_triage_write(
        tmp_path, caplog, key, member):
    block = [line.format(key=key) for line in _FLOW_COMMENT_SHAPES[member]]
    fm = ['company: "Example Foundry"', 'role: "Analyst"', "status: new", "score: 0", *block,
          'relevance_notes: ""']
    # PyYAML's parser accepts the original, with the unindented line inside the key's value, so
    # the row cannot pass on a note that was already broken. `compose` rather than `safe_load`:
    # the colon rows after `]` and `}` use a collection as a mapping key, which the parser accepts
    # and Python's dict construction refuses.
    original = yaml.compose("\n".join(fm), Loader=yaml.SafeLoader)
    assert [k.value for k, _ in original.value] == [
        "company", "role", "status", "score", key, "relevance_notes"]
    v, path = _seed_note(tmp_path, fm)
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert isinstance(yaml.compose("\n".join(lines), Loader=yaml.SafeLoader), yaml.MappingNode)
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any(key in m and "several lines" in m for m in said), said


# Notes PyYAML reads with the key's value continued past its own line, or with a same-named key
# nested earlier, that `core/vault.py::_holds_multiline_value` does not recognise. Only the check
# asking PyYAML whether a single-line write would change how the note reads keeps them unwritten.
_PYYAML_ONLY_SHAPES = {
    "colon-after-spaced-closing-quote": ['{key}: ["a" :#c]', "KEPT-TWO]"],
    "colon-after-explicit-key": ["{key}: [? :#c]", "KEPT-TWO]"],
    "tag-against-flow-punctuation": ["{key}: [!]", "KEPT-TWO]"],
    "same-key-nested-earlier": ["hand_notes:", '  {key}: "kept"', '{key}: "old"'],
}


@pytest.mark.parametrize("key", ["triage_concerns", "culture_flags"])
@pytest.mark.parametrize("shape", sorted(_PYYAML_ONLY_SHAPES))
def test_a_write_pyyaml_reads_as_breaking_the_note_is_left_unwritten(tmp_path, caplog, key, shape):
    block = [line.format(key=key) for line in _PYYAML_ONLY_SHAPES[shape]]
    fm = ['company: "Example Foundry"', 'role: "Analyst"', "status: new", "score: 0", *block,
          'relevance_notes: ""']
    # PyYAML's parser accepts the original, so the row cannot pass on a note already broken, and
    # the scan alone would write it, so the row is held by the PyYAML check and nothing else.
    nested = ["hand_notes"] if shape == "same-key-nested-earlier" else []
    original = yaml.compose("\n".join(fm), Loader=yaml.SafeLoader)
    assert [k.value for k, _ in original.value] == [
        "company", "role", "status", "score", *nested, key, "relevance_notes"]
    assert not _holds_multiline_value("\n".join(fm), key)
    v, path = _seed_note(tmp_path, fm)
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    lines = _frontmatter_lines(path)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert isinstance(yaml.compose("\n".join(lines), Loader=yaml.SafeLoader), yaml.MappingNode)
    if nested:
        assert yaml.safe_load("\n".join(lines))["hand_notes"] == {key: "kept"}
    after = v.read_leads()[0]
    assert after.status == "shortlist" and after.fm["score"] == "81"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    # Named, and not blamed on a value spread over several lines, which these are not.
    assert any(key in m for m in said), said
    assert not any(key in m and "several lines" in m for m in said), said


# The PyYAML check above also holds most hand-typed shapes through a real Vault, so a Vault-level
# row stays green when one rule of the scan is deleted. These rows call the scan directly, so each
# of its rules still has a row that fails without it.
@pytest.mark.parametrize("shape", sorted({**_MULTI_LINE_SHAPES, **_FLOW_COMMENT_SHAPES}))
def test_the_scan_alone_reports_each_hand_typed_shape_as_spanning_several_lines(shape):
    block = [line.format(key="culture_flags")
             for line in {**_MULTI_LINE_SHAPES, **_FLOW_COMMENT_SHAPES}[shape]]
    assert _holds_multiline_value("\n".join(["status: new", *block, 'next_key: "x"']),
                                  "culture_flags")


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
    # unwritten and logged. The same lines can sit under a block-scalar header the helper does
    # not recognise -- the tagged and anchored block scalars in `_MULTI_LINE_SHAPES` look exactly
    # like this below their headers -- and writing over one of those would corrupt it with no
    # warning.
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


@pytest.mark.parametrize("key", ["triage_concerns", "culture_flags"])
@pytest.mark.parametrize("value", [
    pytest.param("[won't sponsor, remote]", id="flow-sequence-apostrophe"),
    pytest.param("{a: don't}", id="flow-mapping-apostrophe"),
])
def test_a_one_line_flow_value_with_a_quote_in_a_plain_item_is_left_unwritten(
        tmp_path, caplog, key, value):
    # The trade-off `core/vault.py::_inline_value_left_open` takes (#329): any quote character
    # outside a quoted run opens one, although YAML opens a quoted scalar only where a node starts.
    # So this complete one-line value counts as spanning several lines, and is left unwritten and
    # logged. A later quote on the same line can close that run again, which is why
    # `balanced-apostrophes` in `_ONE_LINE_VALUES` is written. A rule that opened a run only where a
    # node starts was measured writing over quoted values continued on a later line (after a node
    # property or an explicit key), so a change that writes these rows must still leave those
    # values alone.
    assert isinstance(yaml.safe_load(f"{key}: {value}")[key], (list, dict))
    _left_unwritten(tmp_path, caplog, key, [f"{key}: {value}"],
                    ['relevance_notes: ""'], ['next_key: "x"'])


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
def test_a_relevance_notes_value_continued_under_an_indented_hash_line_survives_an_append(
        tmp_path, caplog, shape):
    # The append path for the `_MULTI_LINE_SHAPES` entries continued on an indented line starting
    # with `#`: a tagged or anchored block scalar, and a quoted scalar continued on an indented
    # line. Each is left undone and logged, never written over.
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


def test_an_append_pyyaml_reads_as_breaking_the_note_is_left_undone(tmp_path, caplog):
    # #329: the append's own guard, for a shape only the PyYAML check catches. The merged note
    # text is safe for frontmatter, so neither the multi-line test nor the unsafe-text arm holds it.
    block = [line.format(key="relevance_notes")
             for line in _PYYAML_ONLY_SHAPES["colon-after-explicit-key"]]
    fm = ['company: "Example Foundry"', 'role: "Analyst"', "status: new", *block, 'next_key: "x"']
    assert [k.value for k, _ in yaml.compose("\n".join(fm), Loader=yaml.SafeLoader).value] == [
        "company", "role", "status", "relevance_notes", "next_key"]
    assert not _holds_multiline_value("\n".join(fm), "relevance_notes")
    v, path = _seed_note(tmp_path, fm)
    with caplog.at_level("WARNING"):
        lines, parsed = _append(path, v)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert parsed["status"] == "research" and parsed["next_key"] == "x"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("relevance_notes" in m for m in said), said
    assert not any("several lines" in m or "unsafe" in m for m in said), said


@pytest.mark.parametrize("header", ["|", ">", "|+", ">+"])
def test_a_missing_key_after_a_final_block_scalar_is_written_without_a_warning(
        tmp_path, caplog, header):
    # #329: a note made before `triage_concerns` existed lacks it, and a property added after the
    # vault's own keys can be a block scalar, so the key is appended after that scalar. Read as the
    # file holds it, final newline included, the scalar's value is the same before and after.
    fm = ['company: "Example Foundry"', 'role: "Analyst"', "status: new", "score: 0",
          'culture_flags: ""', 'relevance_notes: ""', f"hand_notes: {header}", "  typed by hand",
          "  second line"]
    if header.endswith("+"):
        fm.append("")   # the trailing blank line a keep scalar holds on to
    v, path = _seed_note(tmp_path, fm)
    before = yaml.safe_load(_frontmatter_as_held(path))
    assert "triage_concerns" not in before
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, v.read_leads({"new"})[0], dict(_VERDICT), {}) == "applied"
    after = yaml.safe_load(_frontmatter_as_held(path))
    assert after["triage_concerns"] == "; ".join(FRAMING_CONCERNS)
    assert after["hand_notes"] == before["hand_notes"]
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


def test_an_append_onto_a_missing_relevance_notes_after_a_final_block_scalar_lands(
        tmp_path, caplog):
    # The same for the note append, whose PyYAML check applies to every `append_note` caller.
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "hand_notes: |", "  typed by hand"])
    before = yaml.safe_load(_frontmatter_as_held(path))
    assert "relevance_notes" not in before
    with caplog.at_level("WARNING"):
        v.update_fields(v.read_leads()[0].ref, {"status": "research"},
                        append_note=_NOTE, note_tag="[probe]")
    after = yaml.safe_load(_frontmatter_as_held(path))
    assert after["relevance_notes"] == _NOTE and after["status"] == "research"
    assert after["hand_notes"] == before["hand_notes"]
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


def test_triage_writes_onto_an_ordinary_note_land_without_a_warning(tmp_path, caplog):
    # The control for the PyYAML check: a note the vault itself created takes a verdict, and a
    # second verdict over the first one's own values, with nothing left unwritten or logged.
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title="Analyst", company="Example Foundry",
                  url="https://example.invalid/1"))
    second = dict(_VERDICT, concerns=["a second concern"], culture_flags=["a second flag"])
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, v.read_leads({"new"})[0], dict(_VERDICT), {}) == "applied"
        assert apply_verdict(v, v.read_leads()[0], second, {}) == "applied"
    fm = v.read_leads()[0].fm
    assert fm["triage_concerns"] == "a second concern" and fm["culture_flags"] == "a second flag"
    parsed = yaml.safe_load("\n".join(_frontmatter_lines(v.read_leads()[0].ref)))
    assert parsed["triage_concerns"] == "a second concern"
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


@pytest.mark.parametrize("shape,read", [
    pytest.param("double-quoted-col0", "KEPT-ONE KEPT-TWO", id="double-quoted-col0"),
    # Only the `''` escape keeps this one's quote open on the key's own line.
    pytest.param("single-quoted-doubled-quote-col0", "KEPT-ONE' KEPT-TWO",
                 id="single-quoted-doubled-quote-col0"),
    # The bracket that leaves this one open sits behind an anchor.
    pytest.param("anchored-flow-sequence-col0", ["KEPT-ONE", "KEPT-TWO"],
                 id="anchored-flow-sequence-col0"),
    # The bracket that leaves this one open sits behind YAML's bare non-specific tag.
    pytest.param("non-specific-tag-flow-sequence-col0", ["KEPT-ONE", "KEPT-TWO"],
                 id="non-specific-tag-flow-sequence-col0"),
    # PyYAML reads `#c]` as a comment, so the bracket stays open until the unindented line.
    pytest.param("open-bracket", ["KEPT-TWO"], id="open-bracket"),
])
def test_a_relevance_notes_value_its_own_line_leaves_open_survives_an_append(
        tmp_path, caplog, shape, read):
    # #329: the append path for a quoted value or flow collection whose own line leaves it open,
    # continued on an unindented line. No line below it is indented deeper than the key, so only
    # the key's own line shows the value is not finished.
    block = [line.format(key="relevance_notes")
             for line in {**_MULTI_LINE_SHAPES, **_FLOW_COMMENT_SHAPES}[shape]]
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", *block, 'next_key: "x"'])
    with caplog.at_level("WARNING"):
        lines, parsed = _append(path, v)
    start = lines.index(block[0])
    assert lines[start:start + len(block)] == block
    assert parsed["relevance_notes"] == read
    assert parsed["status"] == "research" and parsed["next_key"] == "x"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("relevance_notes" in m and "several lines" in m for m in said), said


# Values whose own line closes every quote and flow collection it opens, so each is written
# normally: an escaped or doubled quote, a `#` or a `]` inside quotes, and a comment after the
# closing quote all belong to a finished value, and a quote inside a plain value opens nothing.
_ONE_LINE_VALUES = {
    "double-quoted": '"closed"',
    # A closed value holding an escape is still written. These two cannot show that the escape
    # is honoured, since a value starting with a quote closes on its first run either way;
    # `double-quoted-escaped-quote-col0` and `single-quoted-doubled-quote-col0` in
    # `_MULTI_LINE_SHAPES` pin that.
    "closed-double-quoted-with-escaped-quotes": '"has \\"escaped\\" quotes"',
    "closed-single-quoted-with-doubled-quote": "'it''s closed'",
    "double-quoted-hash": '"a # not a comment"',
    "double-quoted-trailing-comment": '"closed" # trailing',
    "flow-sequence": "[a, b]",
    "flow-mapping-nested": "{a: 1, b: [2, 3]}",
    "flow-sequence-quoted-bracket": '["x]", y]',
    "plain-with-quote": "it's plain",
    # The second apostrophe closes the run the first one opened, so the check sees it closed.
    "balanced-apostrophes": "[it's, we're]",
    # A `#` inside a plain item, and one inside quotes, starts no comment.
    "flow-sequence-hash-in-plain-item": "[a, b#c]",
    "double-quoted-leading-hash": '"#1 reason"',
    # A `:` inside a plain item is no member: PyYAML reads the `#` after it as part of the item.
    "flow-sequence-colon-hash-in-plain-item": "[a:#b]",
    # A closed value behind a leading node property is still written.
    "tagged-double-quoted": '!!str "closed"',
    "anchored-flow-sequence": "&x [a, b]",
    "non-specific-tag-double-quoted": '! "closed"',
    # What triage itself writes: a `frontmatter_safe` value in double quotes. `frontmatter_safe`
    # refuses `"` and `\`, so a value triage wrote never leaves its quote open.
    "triage-blank": '""',
    "triage-flags": '"positive: a, negative: b"',
}


@pytest.mark.parametrize("key", ["triage_concerns", "culture_flags"])
@pytest.mark.parametrize("shape", sorted(_ONE_LINE_VALUES))
def test_a_value_its_own_line_closes_is_written_normally(tmp_path, caplog, key, shape):
    v, path = _seed_note(tmp_path, ['company: "Example Foundry"', 'role: "Analyst"',
                                    "status: new", "score: 0",
                                    f"{key}: {_ONE_LINE_VALUES[shape]}",
                                    'relevance_notes: ""', 'next_key: "x"'])
    # The seeded note is valid YAML to begin with, so the parse after the write means something.
    assert isinstance(yaml.safe_load("\n".join(_frontmatter_lines(path))), dict)
    note = v.read_leads({"new"})[0]
    with caplog.at_level("WARNING"):
        assert apply_verdict(v, note, dict(_VERDICT), {}) == "applied"
    expected = {"triage_concerns": "; ".join(FRAMING_CONCERNS),
                "culture_flags": ", ".join(FRAMING_FLAGS)}[key]
    assert v.read_leads()[0].fm[key] == expected
    parsed = yaml.safe_load("\n".join(_frontmatter_lines(path)))
    assert parsed[key] == expected
    assert parsed["company"] == "Example Foundry" and parsed["role"] == "Analyst"
    assert parsed["status"] == "shortlist" and parsed["next_key"] == "x"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert not any(key in m for m in said), said


@pytest.mark.parametrize("shape", sorted(_ONE_LINE_VALUES))
def test_the_scan_alone_reports_each_one_line_value_as_fitting_on_one_line(shape):
    # The over-reporting side of the scan, pinned without the PyYAML check in the way.
    assert not _holds_multiline_value(
        "\n".join(["status: new", f"culture_flags: {_ONE_LINE_VALUES[shape]}", 'next_key: "x"']),
        "culture_flags")
