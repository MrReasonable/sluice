from sluice import mcpserver
from sluice.core import formfit


def test_mcpserver_uses_the_shared_form_fit_rules():
    assert mcpserver._DESC_MAX_CHARS is formfit.DESC_MAX_CHARS
    assert mcpserver._entry_lines is formfit.entry_lines
    assert mcpserver._hides_text is formfit.hides_text


def test_hides_text_flags_a_carriage_return_and_a_bidi_override_but_not_newline_or_tab():
    # \u escapes, never raw bidi characters, in this file.
    assert formfit.hides_text("a\rb") and formfit.hides_text("a\u202eb")
    assert not formfit.hides_text("a\nb\tc")
