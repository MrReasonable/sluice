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


def test_fence_is_longer_than_any_backtick_run_in_the_body():
    assert m._fence("plain") == "```"
    assert m._fence("has ``` inside") == "````"
    assert m._fence("has ````` inside") == "``````"


def test_render_form_shows_every_body_in_full_inside_its_own_fence():
    shown = [("example-alpha", "Line with ``` and <!-- 40% --> and [a](b)"),
             ("example-beta", "Second body")]
    msg = m._render_form(shown, "make them citable")
    for title, body in shown:
        assert title in msg
        fence = m._fence(body)
        assert f"{fence}\n{body}\n{fence}" in msg
    assert "make them citable" in msg


def test_form_schema_uses_positional_keys_ticked_by_default():
    schema = m._form_schema([("a title with spaces", "x"), ("entry_9", "y")])
    assert list(schema["properties"]) == ["entry_1", "entry_2"]
    for prop in schema["properties"].values():
        assert prop["type"] == "boolean" and prop["default"] is True
    assert schema["properties"]["entry_1"]["description"] == "a title with spaces"


def test_pack_form_keeps_order_reports_the_rest_and_every_oversize_entry():
    small = [(f"t{i}", "x" * 100) for i in range(5)]
    shown, rest, oversize = m._pack_form(small, budget=350)
    assert [t for t, _ in shown] == ["t0", "t1"]
    assert rest == ["t2", "t3", "t4"] and oversize == []
    shown, rest, oversize = m._pack_form([("big", "x" * 500), ("ok", "y")], budget=350)
    assert [t for t, _ in shown] == ["ok"] and oversize == ["big"] and rest == []
    # An oversize entry AFTER the cut-off is still reported, not silently dropped.
    entries = [("a", "x" * 200), ("b", "x" * 200), ("huge", "x" * 900)]
    shown, rest, oversize = m._pack_form(entries, budget=350)
    assert [t for t, _ in shown] == ["a"] and rest == ["b"] and oversize == ["huge"]


def test_build_form_keeps_the_whole_message_within_budget():
    entries = [(f"t{i}", "x" * 300) for i in range(40)]
    shown, rest, oversize, message = m._build_form(entries, "make them citable", 2000)
    assert shown and rest and not oversize
    assert len(message) <= 2000


def test_a_huge_queue_of_short_entries_still_shows_a_form():
    """The header reserve must not scale with the WHOLE queue, or a big enough backlog of
    short entries leaves no budget for any of them."""
    entries = [(f"t{i}", "x") for i in range(9000)]
    shown, rest, oversize, message = m._build_form(entries, "make them citable", 8000)
    assert shown and len(message) <= 8000 and not oversize


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
