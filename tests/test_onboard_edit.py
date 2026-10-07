import re

import pytest

from sluice.onboard import edit
from sluice.onboard.edit import EditRefused
from sluice.onboard.emit import flow_list, scalar
from sluice.onboard.plan import build_plan
from sluice.onboard.questions import catalogue
from tests.conftest import LOCATIONS, _title_pool

# An `init` file with EVERY key unset (vault_dir included), so set-then-clear can return to it.
INIT = build_plan({}).config_text
_TITLES = _title_pool()
NON_DEFAULT = {"accept_titles": [_TITLES[0]], "reject_titles": [_TITLES[1]],
               "target_locations": [LOCATIONS[0]], "reject_companies": ["Example Co"],
               "contract_floor": 400, "perm_floor": 50000, "lead_ttl_days": 30,
               "min_jd_chars": 200, "relevance_keep": ["Example"], "relevance_drop": ["Other"],
               "listing_languages": ["en"], "backend": "anthropic", "renderer": "script",
               "vault_dir": "/example/other"}


def _render(v):
    return flow_list(v) if isinstance(v, list) else scalar(v)


def _block_text(text, block):
    """Independent of edit.py: a block's lines are those after `block:` up to the next line
    starting at column 0 with a key (not a comment); the root is every column-0 line."""
    if not block:
        return "\n".join(ln for ln in text.splitlines() if ln and not ln[0].isspace())
    m = re.search(rf"^{block}:[ \t]*\n((?:(?:[ \t].*|#.*|)\n)*)", text + "\n", re.M)
    return m.group(1) if m else ""


def _key_line_count(text, dotted):
    """_render_key's two shapes, counted inside the key's own block only."""
    parts = dotted.split(".")
    block, indent, leaf = (parts[0], "  ", parts[1]) if len(parts) == 2 else ("", "", parts[0])
    pat = re.compile(rf"^{indent}(?:# )?{re.escape(leaf)}:(?:[ \t]|$)", re.M)
    return len(pat.findall(_block_text(text, block)))


@pytest.mark.parametrize("q", catalogue(), ids=lambda q: q.key)
def test_set_twice_equals_once_and_set_then_clear_is_byte_identical(q):
    text = INIT
    for dotted in q.writes_to:
        once = edit.set_key(text, dotted, _render(NON_DEFAULT[q.key]))
        assert edit.set_key(once, dotted, _render(NON_DEFAULT[q.key])) == once
        assert _key_line_count(once, dotted) == 1
        assert edit.clear_key(once, dotted) == text
        assert _key_line_count(edit.clear_key(once, dotted), dotted) == 1


def test_clear_of_an_active_key_writes_inits_unset_line():
    text = edit.set_key(INIT, "lead_ttl_days", "30")
    assert edit.unset_line("lead_ttl_days", "") in edit.clear_key(text, "lead_ttl_days")


def test_a_missing_key_is_inserted_at_the_end_of_its_block():
    hand = "triage:\n  # a hand comment\n  backend: \"claude-max\"\ncv:\n  renderer: \"script\"\n"
    out = edit.set_key(hand, "triage.accept_titles", flow_list([_TITLES[0]]))
    assert out == hand.replace('backend: "claude-max"\n',
                               f'backend: "claude-max"\n  accept_titles: {flow_list([_TITLES[0]])}\n')


def test_a_missing_block_is_created_and_a_missing_trailing_newline_is_added():
    out = edit.set_key("lead_ttl_days: 30", "cv.renderer", scalar("script"))
    assert out == 'lead_ttl_days: 30\n\ncv:\n  renderer: "script"\n'


def test_a_missing_root_key_lands_before_the_first_block():
    out = edit.set_key("triage:\n  backend: \"x\"\n", "lead_ttl_days", "30")
    assert out.index("lead_ttl_days: 30") < out.index("triage:")


@pytest.mark.parametrize("bad", [
    "triage:\n  accept_titles:\n    - Example Title\n",        # block list
    "triage:\n  accept_titles: |\n    Example\n",               # block scalar
    "triage:\n  accept_titles: []\n  accept_titles: []\n",     # duplicate
])
def test_shapes_it_cannot_place_are_refused(bad):
    with pytest.raises(EditRefused):
        edit.set_key(bad, "triage.accept_titles", flow_list(["X"]))


def test_add_then_remove_a_search_is_byte_identical_and_the_last_is_refused():
    one = edit.add_search(INIT, "remoteok", "Example search", "https://example.invalid/a")
    two = edit.add_search(one, "remoteok", "Second", "https://example.invalid/b")
    assert edit.remove_search(two, "remoteok", "Second", "https://example.invalid/b") == one
    with pytest.raises(EditRefused, match="last search"):
        edit.remove_search(one, "remoteok", "Example search", "https://example.invalid/a")


@pytest.mark.parametrize("hand", ['sources: {"remoteok": {"searches": []}}\n',
                                  "sources:\n  'remoteok': {searches: []}\n"])
def test_a_sources_block_in_a_form_it_cannot_place_is_refused(hand):
    with pytest.raises(EditRefused, match="cannot place"):
        edit.add_search(hand, "remoteok", "Second", "https://example.invalid/b")


def test_a_searches_entry_not_in_flow_form_is_refused():
    hand = 'sources:\n  "remoteok":\n    searches:\n      - - Example\n        - https://example.invalid\n'
    with pytest.raises(EditRefused, match="flow form"):
        edit.add_search(hand, "remoteok", "Second", "https://example.invalid/b")


def test_crlf_lines_keep_their_endings():
    text = "lead_ttl_days: 1\r\ntriage:\r\n  backend: \"x\"\r\n"
    out = edit.set_key(text, "lead_ttl_days", "30")
    assert out == "lead_ttl_days: 30\r\ntriage:\r\n  backend: \"x\"\r\n"


BARE = "vault_dir:\nlead_ttl_days: 5\ntriage:\n  backend: \"x\"\n"


def test_a_bare_root_key_is_a_key_not_a_block_header():
    assert edit.is_active(BARE, "vault_dir")
    out = edit.set_key(BARE, "vault_dir", '"/x"')
    assert out == BARE.replace("vault_dir:\n", 'vault_dir: "/x"\n')
    assert out.count("vault_dir") == 1


def test_a_bare_root_key_clears_to_inits_unset_line():
    out = edit.clear_key(BARE, "vault_dir")
    assert out == BARE.replace("vault_dir:\n", edit.unset_line("vault_dir", "") + "\n")


def test_a_genuine_block_header_is_still_a_block():
    # `triage:` is followed by an indented key, and (as in init's output) a block whose body is
    # only indented comments is a block too.
    assert edit.is_active(BARE, "triage.backend")
    commented = "triage:\n\n  # backend:   # x\ncv:\n  renderer: \"script\"\n"
    out = edit.set_key(commented, "triage.backend", '"x"')
    assert out.count("triage:") == 1 and out.index('backend: "x"') < out.index("cv:")


MULTILINE_ENTRY = (INIT + "sources:\n  remoteok:\n    searches:\n"
                   "      - [Example search,\n        \"https://example.invalid/a\"]\n"
                   "      - [Second, \"https://example.invalid/b\"]\n")


@pytest.mark.parametrize("op", [edit.add_search, edit.remove_search])
def test_a_flow_entry_spread_over_lines_is_refused_not_raised_as_a_yaml_error(op):
    """Valid YAML as a whole file; one physical line of it is not. The editor must refuse with
    its own reason, since a yaml.YAMLError is not a ValueError and escaped every caller."""
    import yaml
    assert yaml.safe_load(MULTILINE_ENTRY)["sources"]["remoteok"]["searches"][0] == [
        "Example search", "https://example.invalid/a"]
    with pytest.raises(EditRefused, match="flow form"):
        op(MULTILINE_ENTRY, "remoteok", "Second", "https://example.invalid/b")
