"""Pure helpers behind the MCP verify tool. No mcp import: these run in a bare install."""
import hashlib
import types

from sluice import mcpserver as m


def _cap(form=None, url=None):
    return types.SimpleNamespace(form=form, url=url)


def test_can_elicit_needs_the_modern_protocol_and_form_elicitation():
    assert m._can_elicit("2026-07-28", _cap(form={})) is True
    assert m._can_elicit("2026-07-28", _cap()) is True         # a bare {} counts as form
    assert m._can_elicit("2026-07-28", _cap(url={})) is False   # url-only
    assert m._can_elicit("2026-07-28", None) is False           # declared no elicitation
    assert m._can_elicit("2025-06-18", _cap(form={})) is False  # legacy protocol
    assert m._can_elicit(None, _cap(form={})) is False


def test_each_entry_is_shown_in_full_under_its_own_checkbox():
    """Claude Code folds the form MESSAGE after three lines but shows each checkbox
    DESCRIPTION in full (up to ~2,000 characters, any number of lines, as plain text:
    measured 2026-10-06). So the body lives in the description, verbatim."""
    body = "Line one\nwith <!-- 40% --> and [a](b) and ```\nline three"
    schema = m._form_schema([("example-alpha", body), ("example-beta", "short")])
    assert list(schema["properties"]) == ["entry_1", "entry_2"]
    desc = schema["properties"]["entry_1"]["description"]
    assert desc.startswith("example-alpha") and body in desc
    for prop in schema["properties"].values():
        assert prop["type"] == "boolean" and prop["default"] is True


def test_form_message_fits_in_the_three_lines_claude_code_shows():
    message = m._render_form([("a", "x")] * 4, "make them citable")
    assert "\n" not in message and "make them citable" in message
    assert len(message) <= 2 * m._FORM_COLS  # one logical line, at most two wrapped


def test_pack_form_fills_about_one_screen_and_keeps_order():
    entries = [(f"t{i}", "x" * 200) for i in range(10)]   # ~3 wrapped lines + 3 each
    shown, rest, oversize = m._pack_form(entries)
    assert 1 < len(shown) < 10 and not oversize
    assert [t for t, _ in shown] + rest == [t for t, _ in entries]
    assert sum(m._entry_lines(t, b) for t, b in shown) <= m._FORM_LINES


def test_an_entry_too_long_for_a_description_is_never_shown():
    big = "y" * (m._DESC_MAX_CHARS + 1)
    shown, rest, oversize = m._pack_form([("a", "x"), ("big", big), ("b", "x")])
    assert [t for t, _ in shown] == ["a", "b"] and oversize == ["big"] and rest == []


def test_an_entry_taller_than_a_screen_still_gets_a_form_of_its_own():
    """Rare: under the character cap but many short lines. It is shown alone rather
    than sent to the CLI, since nothing about it is hidden -- only scrolling is needed."""
    tall = "\n".join(f"line {i}" for i in range(60))
    shown, rest, oversize = m._pack_form([("tall", tall), ("next", "x")])
    assert [t for t, _ in shown] == ["tall"] and rest == ["next"] and not oversize


def test_a_huge_queue_of_short_entries_still_shows_a_form():
    shown, rest, oversize = m._pack_form([(f"t{i}", "x") for i in range(9000)])
    assert shown and not oversize and len(shown) + len(rest) == 9000


def test_ticked_default_is_a_recorded_decision():
    """Boxes start ticked by the owner's choice (one click for a batch). The guard is
    _approved_keys: a client that leaves a box out of its answer approves nothing --
    it does NOT protect against a client that sends the defaults back as true."""
    assert all(p["default"] is True
               for p in m._form_schema([("a", "x")])["properties"].values())
    assert m._approved_keys({"entry_1": True}) == {"entry_1"}


def test_state_round_trips_and_binds_each_key_to_the_shown_text_hash():
    shown = [("example-alpha", "body a"), ("example-beta", "body b")]
    state = m._decode_state(m._encode_state(
        "experience", shown, rest=["t3", "t4"], not_found=["nope"],
        failed=[["t9", "unreadable"]]))
    assert state["kind"] == "experience" and state["rest"] == ["t3", "t4"]
    assert state["not_found"] == ["nope"] and state["failed"] == [["t9", "unreadable"]]
    assert state["entries"] == [
        ["entry_1", "example-alpha", hashlib.sha256(b"body a").hexdigest()],
        ["entry_2", "example-beta", hashlib.sha256(b"body b").hexdigest()]]


def test_decode_state_returns_none_for_missing_or_malformed():
    for bad in (None, "", "not json", '{"kind": 1}', '{"kind": "x", "entries": "no"}'):
        assert m._decode_state(bad) is None


def test_only_an_explicit_true_approves():
    assert m._approved_keys({"entry_1": True, "entry_2": False}) == {"entry_1"}
    assert m._approved_keys({}) == set()
    assert m._approved_keys(None) == set()
    assert m._approved_keys({"entry_1": 1, "entry_2": "true"}) == set()
