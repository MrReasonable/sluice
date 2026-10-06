"""cv/reply.py: finding the composer's JSON reply and checking its shape (#364 spec \u00a75.2-\u00a75.3)."""
import json
import unicodedata

import pytest

from sluice.cv.reply import Bullet, Reply, extract_json, parse_reply
from tests.work_count import bound_digit_reads

SLOTS = ("R1", "R2")
GOOD = {"profile": "I build reliable systems.",
        "roles": {"R1": [{"text": "Shipped the platform", "cites": ["EF1"]}]},
        "skills": ["Example Query"]}


def _reply_text(obj=GOOD):
    return json.dumps(obj)


def _all_findings(out):
    """Every finding parse_reply raised, wherever it filed it: the returned list when the
    reply is refused, else each bullet's text findings, which a Reply carries for
    cv/selection.py::select to report if it keeps that bullet."""
    if isinstance(out, list):
        return out
    return [f for found in out.bullet_findings.values() for f in found]


def _findings(obj):
    out = _all_findings(parse_reply(obj, SLOTS))
    assert out, "parse_reply raised no finding"
    return out


def _accepted(out):
    """A Reply carrying no finding at all: a bullet whose text was refused also parses to a
    Reply now, so `isinstance(out, Reply)` alone no longer says its text was accepted."""
    return isinstance(out, Reply) and not out.bullet_findings


# --- extract_json -----------------------------------------------------------------------

def test_a_bare_object_is_extracted():
    assert extract_json(_reply_text()) == GOOD


def test_a_fenced_reply_in_the_real_captured_shape_is_extracted():
    # Backends put a newline after the opening fence; raw_decode does not skip it.
    assert extract_json("```json\n" + _reply_text() + "\n```") == GOOD


def test_chat_around_the_reply_is_ignored():
    assert extract_json("Here you go:\n" + _reply_text() + "\nHope that helps!") == GOOD


def test_a_decodable_brace_in_chat_before_a_fenced_reply_does_not_win():
    # The separating row: only the fenced-first, profile-and-roles rule passes it.
    text = "Sure {} here is the CV:\n```json\n" + _reply_text() + "\n```"
    assert extract_json(text) == GOOD


def test_an_echoed_shape_before_a_fenced_reply_does_not_win():
    # The fragment carries profile AND roles and NO placeholder, so neither the
    # profile-and-roles preference nor the placeholder preference can choose between it and
    # the reply: only the fenced-first rule picks the fenced reply. Task 24's witness 11
    # deletes that rule and turns THIS row red. An echo carrying `<profile>` cannot be the
    # separating input: the placeholder preference rejects it whatever the fence order, and
    # this row once used one and stayed green with the fenced-first rule deleted.
    # The placeholder echo stays as a second input, for the placeholder preference's sake.
    for echo in ('{"profile": "", "roles": {}}', '{"profile": "<profile>", "roles": {}}'):
        text = "You asked for " + echo + " so:\n```json\n" + _reply_text() + "\n```"
        assert extract_json(text) == GOOD, echo


def test_a_json_object_in_chat_before_the_reply_does_not_win():
    # Review Focus 3: the model echoes a JSON-looking fragment of the job ad first.
    text = 'The ad said {"team": "platform"} so:\n' + _reply_text()
    assert extract_json(text) == GOOD


def test_a_top_level_array_is_a_finding():
    assert extract_json(json.dumps([GOOD])) == [
        "REPLY: the reply must be one JSON object, not a list"]


def test_a_duplicate_key_is_reported_by_name():
    text = '{"profile": "a", "profile": "b", "roles": {}}'
    assert extract_json(text) == ["REPLY: duplicate key 'profile' -- give each key once"]


def test_no_json_at_all_is_a_finding():
    assert extract_json("I could not write the CV.") == [
        "REPLY: no JSON object in the reply -- reply with one JSON object and nothing else"]


# --- parse_reply ------------------------------------------------------------------------

def test_a_good_reply_parses():
    assert parse_reply(GOOD, SLOTS) == Reply(
        profile="I build reliable systems.",
        roles={"R1": (Bullet("Shipped the platform", ("EF1",)),)},
        skills=("Example Query",))


def test_slot_ids_match_case_insensitively():
    # Review Focus 1: a model that writes "r1" for R1.
    reply = parse_reply({**GOOD, "roles": {"r1": GOOD["roles"]["R1"]}}, SLOTS)
    assert set(reply.roles) == {"R1"}


@pytest.mark.parametrize("obj", [
    {"profile": "x"},                                         # roles absent
    {"profile": "x", "Roles": {}},                            # misspelt at the top level
    {"cv": {"profile": "x", "roles": {}}},                    # nested under another key
])
def test_roles_missing_or_misplaced_is_a_finding(obj):
    assert any('"roles"' in f for f in _findings(obj))


def test_present_but_empty_roles_parses_and_is_left_to_the_selection_rule():
    assert parse_reply({"profile": "x", "roles": {}}, SLOTS).roles == {}


def test_a_missing_or_blank_profile_is_a_finding():
    assert "REPLY: profile missing or empty" in _findings({"roles": {}})[0]
    assert "REPLY: profile missing or empty" in _findings({"profile": " ", "roles": {}})[0]


def test_an_unknown_slot_is_a_finding_that_names_the_valid_ids():
    assert _findings({**GOOD, "roles": {"R9": []}}) == [
        "REPLY: unknown slot 'R9' -- use only the slot ids given (R1, R2)"]


# A role heading is vault text the model was shown beside each slot id. Keying by it is a
# slip about WHICH name, not about content, so it maps to its slot when exactly one slot
# carries it; the mapping adds no model-authored text to the CV.
HEADINGS = {"R1": "Example Alpha", "R2": "Example Beta"}
_BULLETS = GOOD["roles"]["R1"]


def _parse_h(roles, headings=HEADINGS):
    return parse_reply({**GOOD, "roles": roles}, SLOTS, headings)


def test_a_role_heading_key_maps_to_its_slot():
    reply = _parse_h({"Example Alpha": _BULLETS})
    assert isinstance(reply, Reply) and set(reply.roles) == {"R1"}


def test_a_role_heading_key_matches_case_and_whitespace_insensitively():
    reply = _parse_h({"  EXAMPLE   alpha ": _BULLETS})
    assert isinstance(reply, Reply) and set(reply.roles) == {"R1"}


def test_a_heading_shared_by_two_slots_is_never_mapped():
    # Duplicate headings are legal in a layout; ambiguity must not pick a slot.
    out = _parse_h({"Example Alpha": _BULLETS},
                   {"R1": "Example Alpha", "R2": "example  alpha"})
    assert out == ["REPLY: unknown slot 'Example Alpha' -- use only the slot ids given "
                   "(R1, R2)"]


def test_a_key_matching_a_slot_id_wins_over_a_heading_match():
    # R2's heading is spelled "R1": the key "R1" is the id of slot R1, not a heading.
    reply = _parse_h({"R1": _BULLETS}, {"R1": "Example Alpha", "R2": "R1"})
    assert isinstance(reply, Reply) and set(reply.roles) == {"R1"}


def test_a_slot_given_as_its_id_and_its_heading_is_a_finding():
    out = _parse_h({"R1": _BULLETS, "Example Alpha": _BULLETS})
    assert out == ["REPLY: slot R1 given twice (as 'R1' and 'Example Alpha') -- give each "
                   "slot once"]


def test_a_blank_heading_names_nothing_a_blank_key_could_match():
    out = _parse_h({"": _BULLETS, "  ": _BULLETS}, {"R1": "", "R2": "Example Beta"})
    assert out == ["REPLY: unknown slot '' -- use only the slot ids given (R1, R2)",
                   "REPLY: unknown slot '  ' -- use only the slot ids given (R1, R2)"]


def test_a_heading_that_is_not_text_is_blank_not_its_spelling():
    # str(None) is "None": a non-str heading must not be matchable by that key.
    out = _parse_h({"None": _BULLETS}, {"R1": None, "R2": "Example Beta"})
    assert out == ["REPLY: unknown slot 'None' -- use only the slot ids given (R1, R2)"]


def test_no_headings_given_means_no_heading_mapping():
    assert isinstance(parse_reply({**GOOD, "roles": {"Example Alpha": _BULLETS}}, SLOTS),
                      list)


@pytest.mark.parametrize("bullets,expected", [
    ("not a list", "REPLY: R1 must be a list of bullets"),
    (["not an object"], 'REPLY: R1 bullet 1 must be an object with "text" and "cites"'),
    ([{"text": " ", "cites": ["EF1"]}], "REPLY: R1 bullet 1 has no text"),
    ([{"text": "x", "cites": "EF1"}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
    ([{"text": "x", "cites": [1]}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
])
def test_malformed_bullets_are_findings(bullets, expected):
    assert _findings({**GOOD, "roles": {"R1": bullets}}) == [expected]


def test_a_cite_with_stray_whitespace_is_the_entry_id():
    # A model writing " EF1 " means EF1: kept unstripped it would match no bundle entry and
    # cost the retry as a BAD CITATION.
    out = parse_reply({**GOOD, "roles": {"R1": [{"text": "Shipped the platform",
                                                 "cites": [" EF1 "]}]}}, SLOTS)
    assert _accepted(out) and out.roles["R1"] == (Bullet("Shipped the platform", ("EF1",)),)


@pytest.mark.parametrize("text", ["Cut costs [40%] in a year", "Shipped it [EF1]"])
def test_a_bracket_in_bullet_text_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        'REPLY: R1 bullet 1 contains a bracket -- put entry ids in "cites", never in the text']


def test_a_bracket_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "Grew [500] users."}) == [
        'REPLY: profile contains a bracket -- put entry ids in "cites", never in the text']


def test_a_bullet_whose_text_is_refused_is_kept_with_its_findings_filed_by_position():
    # Whether the finding counts is cv/selection.py::select's call: only it knows whether
    # the bullet survives the budget. Filed under (slot, 1-based position), the numbering
    # select and every finding message use.
    good, bad = {"text": "Shipped it", "cites": ["EF1"]}, {"text": "Grew 2,5x", "cites": ["EF1"]}
    out = parse_reply({**GOOD, "roles": {"R1": [good, bad]}}, SLOTS)
    assert isinstance(out, Reply)
    assert out.roles["R1"] == (Bullet("Shipped it", ("EF1",)), Bullet("Grew 2,5x", ("EF1",)))
    assert out.bullet_findings == {("R1", 2): (
        "REPLY: R1 bullet 2 writes a decimal with a comma -- use a point, e.g. 2.5",)}


def test_a_reply_refused_on_other_grounds_still_names_each_bullets_text_finding():
    # With no selection there is nothing to say which bullet would be trimmed, so the
    # refusal carries every finding, in reply order.
    out = parse_reply({**GOOD, "profile": "Grew [500] users.",
                       "roles": {"R1": [{"text": "Grew 2,5x", "cites": ["EF1"]}]}}, SLOTS)
    assert out == [
        'REPLY: profile contains a bracket -- put entry ids in "cites", never in the text',
        "REPLY: R1 bullet 1 writes a decimal with a comma -- use a point, e.g. 2.5"]


@pytest.mark.parametrize("text", ["line one\nline two", "a\u2028b", "a\x1bb"])
def test_a_line_break_or_control_character_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains a line break or control character -- write it as one line"]


@pytest.mark.parametrize("text", ["Grew to 2\u200b5 users", "a\u200db", "a\u2060b",
                                  "a\u202eb", "a\u00adb"])
def test_an_invisible_format_character_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains an invisible formatting character -- write plain text"]


def test_an_invisible_format_character_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "I grew it to 2\u200b5."}) == [
        "REPLY: profile contains an invisible formatting character -- write plain text"]


def test_a_text_equal_to_a_section_heading_is_a_finding():
    assert _findings({**GOOD, "profile": "Work Experience"}) == [
        "REPLY: profile is a section heading, not content"]


@pytest.mark.parametrize("where", ["profile", "bullet", "skill"])
def test_an_echoed_placeholder_is_a_finding(where):
    obj = json.loads(json.dumps(GOOD))
    if where == "profile":
        obj["profile"] = "<profile>"
    elif where == "bullet":
        obj["roles"]["R1"][0]["text"] = "<bullet>"
    else:
        obj["skills"] = ["<skill from the list>"]
    assert any("example placeholder" in f for f in _findings(obj))


def test_a_malformed_skills_value_is_flagged_never_refused():
    reply = parse_reply({**GOOD, "skills": "Example Query"}, SLOTS)
    assert (reply.skills, reply.skills_malformed) == ((), True)


def test_missing_skills_means_none():
    reply = parse_reply({k: v for k, v in GOOD.items() if k != "skills"}, SLOTS)
    assert (reply.skills, reply.skills_malformed) == ((), False)


def test_unknown_keys_are_ignored_at_the_top_level_and_inside_a_bullet():
    obj = {**GOOD, "notes": "x",
           "roles": {"R1": [{"text": "Shipped it", "cites": ["EF1"], "title": "x"}]}}
    assert _accepted(parse_reply(obj, SLOTS))


# --- hardening: pure and never raising on reply content ---------------------------------

def _deep(n):
    v = []
    for _ in range(n):
        v = [v]
    return v


def test_a_deeply_nested_extra_key_is_not_walked():
    assert _accepted(parse_reply({**GOOD, "extra": _deep(3000)}, SLOTS))


def test_a_deeply_nested_reply_text_is_a_finding_not_a_raise():
    text = '{"profile": "x", "roles": {}, "extra": ' + "[" * 100000 + "]" * 100000 + "}"
    out = extract_json(text)
    assert isinstance(out, list) and out[0].startswith("REPLY:")


@pytest.mark.parametrize("text", ['{"a":' * 100000, "[" * 100000], ids=["objects", "arrays"])
def test_pathological_nesting_is_a_finding_not_a_raise(text):
    out = extract_json(text)
    assert isinstance(out, list) and out[0].startswith("REPLY:")


def _count_decodes(monkeypatch):
    """Wrap `json.JSONDecoder.raw_decode` -- the call `cv/reply.py::_candidates` makes per
    attempt, looked up on the class -- and return a one-element list holding the count."""
    original = json.JSONDecoder.raw_decode
    count = [0]

    def counting(self, s, idx=0):
        count[0] += 1
        return original(self, s, idx)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", counting)
    return count


def test_the_worst_inputs_at_the_cap_are_bounded_by_counted_decode_attempts(monkeypatch):
    # Each failed raw_decode builds a JSONDecodeError that counts lines up to its position,
    # so the scan's work is (attempts x reply length), and the length is capped. Counting
    # the attempts measures that work on any host, traced by --cov or not; a wall-clock
    # bound here measured the runner instead.
    from sluice.cv import reply
    count = _count_decodes(monkeypatch)
    # Scope first: the wrapper must see the calls the scan makes, or every bound below
    # passes on a count that never moves.
    assert isinstance(extract_json(_reply_text()), dict) and count[0] >= 1

    cap = reply._MAX_REPLY_CHARS
    worst = {"braces": "{" * cap, "keys": '{"' * (cap // 2),
             "open-string": '{"a":"' + "{" * (cap - 10)}
    seen = {}
    for name, text in worst.items():
        count[0] = 0
        out = extract_json(text)
        assert isinstance(out, list) and out[0].startswith("REPLY:"), (name, out)
        seen[name] = count[0]
    # The attempt budget: every brace in "keys" passes the prefilter, so only the budget
    # stops the scan short of one attempt per brace.
    assert max(seen.values()) <= reply._MAX_DECODE_ATTEMPTS, seen
    # The prefilter: no brace in "braces" can open an object, so none costs an attempt.
    # Without it the budget still holds the count, so the budget row cannot witness this.
    assert seen["braces"] == 0, seen


def test_a_reply_over_the_cap_is_a_finding():
    out = extract_json(_reply_text() + " " * 300000)
    assert isinstance(out, list) and "too long" in out[0]


def test_a_reply_nested_under_another_key_is_a_finding():
    out = extract_json(json.dumps({"cv": GOOD}))
    assert isinstance(out, list) and out[0].startswith("REPLY:")


@pytest.mark.parametrize("text", ["2\u034f5", "2\ufe0f5", "2\u3164" + "5", "2\U000e0101" + "5"])
def test_a_default_ignorable_character_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains an invisible formatting character -- write plain text"]


def test_a_duplicate_key_in_a_chat_fragment_does_not_refuse_the_valid_reply():
    text = 'Ad said {"a": 1, "a": 2} so:\n' + _reply_text()
    assert extract_json(text) == GOOD


def test_an_unfenced_echo_before_a_bare_reply_does_not_win():
    echo = '{"profile": "<profile>", "roles": {}}'
    assert extract_json("Shape " + echo + " so:\n" + _reply_text()) == GOOD


def test_an_echo_in_the_first_of_two_fences_does_not_win():
    echo = '{"profile": "<profile>", "roles": {}}'
    text = "```json\n" + echo + "\n```\nthen\n```json\n" + _reply_text() + "\n```"
    assert extract_json(text) == GOOD


def test_an_echo_alone_still_reaches_the_placeholder_finding():
    echo = {"profile": "<profile>", "roles": {}}
    assert extract_json(json.dumps(echo)) == echo
    assert any("placeholder" in f for f in _findings(echo))


def test_a_slot_given_twice_by_case_is_a_finding():
    b = GOOD["roles"]["R1"]
    out = _findings({**GOOD, "roles": {"R1": b, "r1": b}})
    assert len(out) == 1 and "twice" in out[0] and "R1" in out[0]


@pytest.mark.parametrize("text", ["WORK  EXPERIENCE", "Work Experience:", "work\u00a0experience"])
def test_a_heading_with_spacing_or_colon_is_a_finding(text):
    assert _findings({**GOOD, "profile": text}) == [
        "REPLY: profile is a section heading, not content"]


def test_an_unassigned_code_point_is_a_finding():
    text = "2" + chr(0x2065) + "5"
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains an invisible formatting character -- write plain text"]


@pytest.mark.parametrize("text", ["Grew to \u2473 engineers", "Grew to \u216b engineers",
                                  "Cut costs by \u00be"])
def test_a_number_written_without_digits_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 writes a number without digits -- write numbers with the digits 0-9"]


@pytest.mark.parametrize("text", ["Led \u4e09 teams", "Grew to \u56db\u5341 engineers",
                                  "Hired \u3007 contractors"])
def test_a_cjk_numeral_is_a_finding(text):
    # Category Lo -- a letter to Unicode -- yet it means a number figures() cannot read.
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 writes a number without digits -- write numbers with the digits 0-9"]


@pytest.mark.parametrize("text", ["Opened the \u793e office", "Ran \u65e5\u672c sales"])
def test_a_cjk_character_with_no_numeric_value_is_not_refused(text):
    assert _accepted(_bullet(text))


@pytest.mark.parametrize("n", range(1, 10))
def test_circled_digits_one_to_nine_are_read_not_refused(n):
    # U+2460-U+2468 carry digit values, so core/tokens.py::figures reads them.
    from sluice.core.tokens import figures
    text = f"Grew to {chr(0x245F + n)} teams"
    assert _accepted(_bullet(text)) and figures(text) == {str(n)}


@pytest.mark.parametrize("text,code", [
    ("Ran \uff25xamplelang daily", "U+FF25"),
    ("Ran Exampl\u0435lang daily", "U+0435"),
    ("Ran Examplel\u03b1ng daily", "U+03B1"),
    ("Ran \U0001d400xamplelang daily", "U+1D400"),
])
def test_a_look_alike_letter_is_a_finding_naming_its_code_point(text, code):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 uses a look-alike character {code} -- write it as a plain letter"]


@pytest.mark.parametrize("text", [
    "Cut latency to 200\u00b5s", "Cut latency to 200\u03bcs", "Served 10\u00a0000 users",
    "Improved it\u2026", "Moved to Examplelang\u2122", "Covered 40 m\u00b2",
    "Held at 40\u2103", "Cafe\u0301 rollout"])
def test_text_copied_faithfully_from_evidence_is_not_refused(text):
    out = parse_reply({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}, SLOTS)
    assert _accepted(out), out


def test_a_standalone_greek_word_is_not_refused():
    greek = "\u03b1\u03b2\u03b3"
    out = parse_reply({**GOOD, "profile": f"I use the {greek} notation."}, SLOTS)
    assert _accepted(out)


@pytest.mark.parametrize("sep", ["\u00b7", "\u22c5", "\u2219", "\u066b", "\u066c", "\u201a",
                                 "'", "\u2019", "\u2e31"])
def test_a_look_alike_separator_between_digits_is_a_finding(sep):
    text = f"Grew revenue 8{sep}3x"
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 separates digits with U+{ord(sep):04X} -- use . or , between digits"]


@pytest.mark.parametrize("text", ["Grew 3-8x", "Since 02/2023", "A 1:1 ratio", "Served 10\u00a0000",
                                  "Grew 8.3x", "Served 3,000 users"])
def test_an_ordinary_separator_between_digits_is_not_refused(text):
    out = parse_reply({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}, SLOTS)
    assert _accepted(out), out


def test_a_circled_digit_is_read_as_a_figure_not_refused():
    # unicodedata.digit gives U+2460 the value 1, so core/tokens.py::figures already sees it.
    out = parse_reply({**GOOD, "roles": {"R1": [{"text": "Grew to \u2460 team",
                                                 "cites": ["EF1"]}]}}, SLOTS)
    assert _accepted(out)


def _bullet(text):
    return parse_reply({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}, SLOTS)


@pytest.mark.parametrize("text", ["Grew 2,5x", "Grew to 3,8 million", "Grew 12,34 units"])
def test_a_comma_that_is_not_a_group_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 writes a decimal with a comma -- use a point, e.g. 2.5"]


@pytest.mark.parametrize("text", ["Served 3,000 users", "Served 1,000,000 users",
                                  "Served 50,000 users", "Grew 2.5x, then 7, then 9"])
def test_a_comma_that_groups_or_ends_a_clause_is_not_refused(text):
    assert _accepted(_bullet(text))


@pytest.mark.parametrize("sp", ["\u200a", "\u2006", "\u2008", "\u2000", "\u2007", "\u205f",
                                "\u1680", "\u3000"])
def test_exotic_whitespace_between_digits_is_a_finding(sp):
    assert _findings({**GOOD, "roles": {"R1": [{"text": f"Grew 8{sp}3x", "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 separates digits with U+{ord(sp):04X} -- use . or , between digits"]


@pytest.mark.parametrize("text", ["Served 10\u00a0000 users", "Served 10\u202f000 users",
                                  "Served 10\u2009000 users"])
def test_grouping_whitespace_between_digits_is_not_refused(text):
    assert _accepted(_bullet(text))


def test_a_grouping_space_not_in_the_grouping_shape_is_refused():
    assert _findings({**GOOD, "roles": {"R1": [
        {"text": "Grew 8\u00a03x", "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 separates digits with U+00A0 -- use . or , between digits"]


@pytest.mark.parametrize("text,code", [("Zorbat\u0585ol daily", "U+0585"),
                                       ("Kubec\u13a4rp daily", "U+13A4"),
                                       ("K\u00b5becorp daily", "U+00B5"),
                                       ("K\u03bcbecorp daily", "U+03BC")])
def test_a_latin_word_mixing_any_other_script_is_a_finding(text, code):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 uses a look-alike character {code} -- write it as a plain letter"]


@pytest.mark.parametrize("text", ["Cut it to 200\u00b5s", "Measured in \u00b5s", "Took 5 \u03bcs"])
def test_a_micro_sign_opening_a_unit_word_is_not_refused(text):
    assert _accepted(_bullet(text))


# Cyrillic capital O and Greek capital omicron, built with chr() so no look-alike sits in
# this file's source.
_CYRILLIC_O, _GREEK_O = chr(0x041E), chr(0x039F)


@pytest.mark.parametrize("letter", [_CYRILLIC_O, _GREEK_O])
@pytest.mark.parametrize("shape", ["Grew revenue 8{}% in a year", "Grew 1{}{}% in a year",
                                   "Grew revenue {}8% in a year"])
def test_a_non_latin_letter_against_a_digit_is_a_finding_in_a_bullet_and_the_profile(
        letter, shape):
    # "8O%" shows the page 80% while figures() reads only the 8. The word scan alone
    # misses it: its pattern excludes digits, so the letter is a one-letter word.
    text = shape.replace("{}", letter)
    code = f"U+{ord(letter):04X}"
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 uses a look-alike character {code} -- write it as a plain letter"]
    assert _findings({**GOOD, "profile": text}) == [
        f"REPLY: profile uses a look-alike character {code} -- write it as a plain letter"]


# What renders as no gap is looked through: a combining mark, whitespace other than an
# ASCII space, and one figure separator ("8.O" reads 8.0, "8-O" a range). A digit is what
# core/tokens.py::digit_value says, the predicate figures() reads runs with, so a full-width,
# Arabic-Indic or superscript digit counts on either side.
@pytest.mark.parametrize("text", [
    f"Grew 8{chr(0x0301)}{_CYRILLIC_O}%", f"Grew 8{chr(0x00A0)}{_CYRILLIC_O}%",
    f"Grew 8{chr(0x2009)}{_CYRILLIC_O}%", f"Grew 8{chr(0x202F)}{_CYRILLIC_O}%",
    f"Grew 8{chr(0x200A)}{_CYRILLIC_O}%", f"Grew 2.{_CYRILLIC_O}%",
    f"Hired 3-{_CYRILLIC_O} people", f"Grew {_CYRILLIC_O}.8%",
    f"Grew 8.{chr(0x0301)}{_CYRILLIC_O}%", f"Grew {chr(0xFF18)}{_CYRILLIC_O}%",
    f"Grew {chr(0x0668)}{_CYRILLIC_O}%", f"Grew {chr(0x00B2)}{_CYRILLIC_O}%",
    f"Grew {_CYRILLIC_O}{chr(0xFF18)}%"])
def test_a_non_latin_letter_is_against_a_digit_through_what_renders_as_no_gap(text):
    out = _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}})
    assert out == [
        "REPLY: R1 bullet 1 uses a look-alike character U+041E -- write it as a plain letter"]


def test_the_digit_rule_and_figures_agree_on_every_digit_beside_the_letter():
    # The differential: wherever figures() reads a digit at a position beside the letter,
    # the reply check refuses -- one predicate, so neither can end a run the other reads on.
    from sluice.core.tokens import digit_value, figures
    digits = [chr(c) for c in range(0x110000)
              if unicodedata.digit(chr(c), None) is not None
              and not unicodedata.category(chr(c)).startswith("C")]
    assert len(digits) > 100, "the sweep found no digits to check"
    missed = []
    for d in digits:
        for text in (f"Grew {d}{_CYRILLIC_O}%", f"Grew {_CYRILLIC_O}{d}%"):
            assert figures(text) and digit_value(text, text.index(d)) is not None
            if _accepted(_bullet(text)):
                missed.append(f"U+{ord(d):04X}")
    assert missed == []


@pytest.mark.parametrize("ohm", [chr(0x03A9), chr(0x2126)])
@pytest.mark.parametrize("shape", ["Fitted a 10{}", "Fitted a 10{} resistor"])
def test_the_ohm_after_a_digit_is_a_unit_not_a_look_alike(ohm, shape):
    # Greek capital omega and the OHM SIGN, allowed after a digit as micro and mu are: a
    # faithful copy of a unit, and neither reads as a digit. A Cyrillic O there still does.
    assert _accepted(_bullet(shape.format(ohm)))
    assert not _accepted(_bullet(shape.format(_CYRILLIC_O)))


@pytest.mark.parametrize("text", [f"Grew 8 {_CYRILLIC_O}%", f"Grew 8.. {_CYRILLIC_O}%",
                                  f"Grew 8..{_CYRILLIC_O}%",
                                  f"Ran the {_CYRILLIC_O}{chr(0x041A)} desk for 3 years",
                                  f"Grew 3 {chr(0x03B1)}{chr(0x03B2)}{chr(0x03B3)} teams",
                                  "Grew revenue 8O% in a year"])
def test_non_latin_text_apart_from_digits_and_ascii_o_are_not_refused_by_the_digit_rule(text):
    # A non-Latin word separated from a figure is ordinary text. The ASCII O against a digit
    # is the accepted residual: Latin, and "5G" or "O2" are real text.
    assert _accepted(_bullet(text))


@pytest.mark.parametrize("text,shown", [("Led 3 100-person teams", "3 100"),
                                        ("Served 10 000 users", "10 000"),
                                        ("Served 1 000 000 users", "1 000 000"),
                                        ("Served \uff11\uff10 \uff10\uff10\uff10 users", "10 000")])
def test_an_ascii_space_grouped_number_is_a_finding(text, shown):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 writes {shown} with a space -- write {shown.replace(' ', ',')} "
        "if it is one number, or reword so two numbers are not side by side"]


def test_an_ascii_space_grouped_number_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "Led 3 100-person teams."}) == [
        "REPLY: profile writes 3 100 with a space -- write 3,100 if it is one number, or "
        "reword so two numbers are not side by side"]


@pytest.mark.parametrize("text", ["Shipped in Q3 2023", "In 2023 100 engineers joined",
                                  "from 2019 to 2023", "In Q3 120 customers signed",
                                  "Hired 3\u20135 engineers"])
def test_ordinary_adjacent_numbers_and_an_en_dash_range_are_not_refused(text):
    assert _accepted(_bullet(text)), _bullet(text)


@pytest.mark.parametrize("text,shown", [("Saved USD3 100 a month", "3 100"),
                                        ("Saved 1,000 000 a year", "1,000 000")])
def test_a_grouped_number_after_a_code_or_a_comma_group_is_a_finding(text, shown):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 writes {shown} with a space -- write {shown.replace(' ', ',')} "
        "if it is one number, or reword so two numbers are not side by side"]


@pytest.mark.parametrize("text", ["Grew 1.5 100-node clusters", "In Q3\u00a0120 customers",
                                  "In Q3\u202f120 customers", "a multiple of x1 000"])
def test_a_decimal_fraction_or_a_lone_letter_label_never_heads_a_group(text):
    # "x1 000" is group_reading's stated residual, pinned here as allowed.
    assert _accepted(_bullet(text)), _bullet(text)


@pytest.mark.parametrize("text", ["Saved R10,000", "Grew x1,000 fold"])
def test_a_comma_group_after_a_letter_is_not_a_decimal_comma(text):
    # Advising "R10.000" would read as 10.000 and become an invented metric.
    assert _accepted(_bullet(text))


def test_a_huge_comma_chain_never_raises():
    out = _bullet("Served 1" + ",000" * 1500 + " users")
    assert isinstance(out, (Reply, list))


@pytest.mark.parametrize("sep", [",", " "])
def test_a_60k_group_chain_is_parsed_in_linear_time(sep, monkeypatch):
    # The reply's bullet checks consult core/tokens.py::group_reading at every separator,
    # and a walk back along the chain from each one was quadratic. Bounded by counted digit
    # reads, not wall-clock time; the reason and the 8-per-character bound are stated in
    # tests/test_core_tokens.py::test_a_60k_group_chain_is_read_in_linear_time (this pass
    # measured under 5 per character, reply.py reading digits of its own on top).
    text = "Served 1" + (sep + "000") * 15000 + " users"
    reply = {**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}
    reads = bound_digit_reads(monkeypatch, 8 * len(text))
    out = parse_reply(reply, SLOTS)
    assert reads[0] <= 8 * len(text)
    assert isinstance(out, (Reply, list))
