"""`set_frontmatter_line`: the guarded one-line edit in-session setup applies to a store note.

Every refusal here is a shape `update_fields` already refuses; the setter must not be a looser
second writer. Values are synthetic and hosts are `example.invalid`."""
import pytest

from sluice.core.vault import FrontmatterEditRefused, parse_frontmatter, set_frontmatter_line

NOTE = '---\nforenames: ""\nemail: "ada@example.invalid"\n---\n# Candidate Profile\n'


def test_sets_one_field_and_leaves_the_rest_byte_identical():
    out = set_frontmatter_line(NOTE, "email", '"example.person@example.invalid"')
    assert parse_frontmatter(out)["email"] == "example.person@example.invalid"
    assert out.replace('"example.person@example.invalid"', '"ada@example.invalid"') == NOTE


def test_refuses_a_duplicate_key_because_the_reader_takes_the_last_copy():
    dup = NOTE.replace("---\n# C", 'email: "distinctive@example.invalid"\n---\n# C')
    with pytest.raises(FrontmatterEditRefused, match="more than once"):
        set_frontmatter_line(dup, "email", '"x@example.invalid"')


def test_refuses_a_multi_line_value():
    multi = NOTE.replace('forenames: ""', "forenames:\n  - Alfa\n  - Bravo")
    with pytest.raises(FrontmatterEditRefused, match="several lines"):
        set_frontmatter_line(multi, "forenames", '"Alfa"')


def test_refuses_windows_line_endings_the_reader_cannot_read():
    with pytest.raises(FrontmatterEditRefused, match="line endings"):
        set_frontmatter_line(NOTE.replace("\n", "\r\n"), "email", '"x@example.invalid"')


def test_refuses_a_note_with_no_frontmatter():
    with pytest.raises(FrontmatterEditRefused, match="no frontmatter"):
        set_frontmatter_line("# Candidate Profile\n", "email", '"x@example.invalid"')


def test_refusal_is_a_value_error():
    assert issubclass(FrontmatterEditRefused, ValueError)


@pytest.mark.parametrize("literal", ["[unclosed", "a: b"])
def test_refuses_a_write_that_would_break_the_note(literal):
    """The stored value is one line and the key appears once, so only
    `_single_line_write_breaks_note` stands between this literal and a note PyYAML no longer
    reads: an unclosed flow sequence, and a second `: ` that makes the line an invalid mapping."""
    with pytest.raises(FrontmatterEditRefused, match="would break the note"):
        set_frontmatter_line(NOTE, "email", literal)
