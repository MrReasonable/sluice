"""cv/reply.py: finding the composer's JSON reply and checking its shape (spec \u00a75.2-\u00a75.3)."""
import json

import pytest

from sluice.cv.reply import Bullet, Reply, extract_json, parse_reply

SLOTS = ("R1", "R2")
GOOD = {"profile": "I build reliable systems.",
        "roles": {"R1": [{"text": "Shipped the platform", "cites": ["EF1"]}]},
        "skills": ["Example Query"]}


def _reply_text(obj=GOOD):
    return json.dumps(obj)


def _findings(obj):
    out = parse_reply(obj, SLOTS)
    assert isinstance(out, list), out
    return out


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
    # The fragment carries profile AND roles, so the profile-and-roles preference cannot
    # choose between it and the reply: only the fenced-first rule picks the fenced reply.
    # Task 24's witness 11 deletes that rule and must turn THIS row red.
    echo = '{"profile": "<profile>", "roles": {}}'
    text = "You asked for " + echo + " so:\n```json\n" + _reply_text() + "\n```"
    assert extract_json(text) == GOOD


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


def test_an_unknown_slot_is_a_finding():
    assert _findings({**GOOD, "roles": {"R9": []}}) == [
        "REPLY: unknown slot 'R9' -- use only the slot ids given"]


@pytest.mark.parametrize("bullets,expected", [
    ("not a list", "REPLY: R1 must be a list of bullets"),
    (["not an object"], 'REPLY: R1 bullet 1 must be an object with "text" and "cites"'),
    ([{"text": " ", "cites": ["EF1"]}], "REPLY: R1 bullet 1 has no text"),
    ([{"text": "x", "cites": "EF1"}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
    ([{"text": "x", "cites": [1]}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
])
def test_malformed_bullets_are_findings(bullets, expected):
    assert _findings({**GOOD, "roles": {"R1": bullets}}) == [expected]


@pytest.mark.parametrize("text", ["Cut costs [40%] in a year", "Shipped it [EF1]"])
def test_a_bracket_in_bullet_text_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        'REPLY: R1 bullet 1 contains a bracket -- put entry ids in "cites", never in the text']


def test_a_bracket_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "Grew [500] users."}) == [
        'REPLY: profile contains a bracket -- put entry ids in "cites", never in the text']


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
    assert isinstance(parse_reply(obj, SLOTS), Reply)


# --- hardening: pure and never raising on reply content ---------------------------------

def _deep(n):
    v = []
    for _ in range(n):
        v = [v]
    return v


def test_a_deeply_nested_extra_key_is_not_walked():
    assert isinstance(parse_reply({**GOOD, "extra": _deep(3000)}, SLOTS), Reply)


def test_a_deeply_nested_reply_text_is_a_finding_not_a_raise():
    text = '{"profile": "x", "roles": {}, "extra": ' + "[" * 100000 + "]" * 100000 + "}"
    out = extract_json(text)
    assert isinstance(out, list) and out[0].startswith("REPLY:")


@pytest.mark.parametrize("text", ['{"a":' * 100000, "[" * 100000], ids=["objects", "arrays"])
def test_pathological_nesting_is_a_finding_not_a_raise(text):
    out = extract_json(text)
    assert isinstance(out, list) and out[0].startswith("REPLY:")


def test_the_worst_inputs_at_the_cap_return_quickly_with_a_finding():
    # Each failed raw_decode builds a JSONDecodeError that counts lines up to its position,
    # so an unbounded number of attempts is quadratic in time. The bound is tight enough
    # that dropping the prefilter, the attempt budget or the cap turns this red.
    import time

    from sluice.cv import reply
    cap = reply._MAX_REPLY_CHARS
    worst = ["{" * cap, '{"' * (cap // 2), '{"a":"' + "{" * (cap - 10)]
    start = time.monotonic()
    outs = [extract_json(t) for t in worst]
    elapsed = time.monotonic() - start
    assert all(isinstance(o, list) and o[0].startswith("REPLY:") for o in outs), outs
    assert elapsed < 1.0, elapsed


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


def test_a_cjk_ideograph_is_a_letter_and_is_not_refused():
    # Accepted residual: a CJK numeral (\u56db\u5341) in model text is a letter to this rule,
    # so it is not seen as a figure. Refusing it would refuse an employer name holding \u4e09.
    for text in ("Grew to \u56db\u5341 engineers", "Worked at \u4e09 Example"):
        out = parse_reply({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}, SLOTS)
        assert isinstance(out, Reply)


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
    assert isinstance(out, Reply), out


def test_a_standalone_greek_word_is_not_refused():
    greek = "\u03b1\u03b2\u03b3"
    out = parse_reply({**GOOD, "profile": f"I use the {greek} notation."}, SLOTS)
    assert isinstance(out, Reply)


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
    assert isinstance(out, Reply), out


def test_a_circled_digit_is_read_as_a_figure_not_refused():
    # unicodedata.digit gives U+2460 the value 1, so core/tokens.py::figures already sees it.
    out = parse_reply({**GOOD, "roles": {"R1": [{"text": "Grew to \u2460 team",
                                                 "cites": ["EF1"]}]}}, SLOTS)
    assert isinstance(out, Reply)


def _bullet(text):
    return parse_reply({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}, SLOTS)


@pytest.mark.parametrize("text", ["Grew 2,5x", "Grew to 3,8 million", "Grew 12,34 units"])
def test_a_comma_that_is_not_a_group_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 writes a decimal with a comma -- use a point, e.g. 2.5"]


@pytest.mark.parametrize("text", ["Served 3,000 users", "Served 1,000,000 users",
                                  "Served 50,000 users", "Grew 2.5x, then 7, then 9"])
def test_a_comma_that_groups_or_ends_a_clause_is_not_refused(text):
    assert isinstance(_bullet(text), Reply)


@pytest.mark.parametrize("sp", ["\u200a", "\u2006", "\u2008", "\u2000", "\u2007", "\u205f",
                                "\u1680", "\u3000"])
def test_exotic_whitespace_between_digits_is_a_finding(sp):
    assert _findings({**GOOD, "roles": {"R1": [{"text": f"Grew 8{sp}3x", "cites": ["EF1"]}]}}) == [
        f"REPLY: R1 bullet 1 separates digits with U+{ord(sp):04X} -- use . or , between digits"]


@pytest.mark.parametrize("text", ["Served 10\u00a0000 users", "Served 10\u202f000 users",
                                  "Served 10\u2009000 users"])
def test_grouping_whitespace_between_digits_is_not_refused(text):
    assert isinstance(_bullet(text), Reply)


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
    assert isinstance(_bullet(text), Reply)


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
    assert isinstance(_bullet(text), Reply), _bullet(text)


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
    assert isinstance(_bullet(text), Reply), _bullet(text)


@pytest.mark.parametrize("text", ["Saved R10,000", "Grew x1,000 fold"])
def test_a_comma_group_after_a_letter_is_not_a_decimal_comma(text):
    # Advising "R10.000" would read as 10.000 and become an invented metric.
    assert isinstance(_bullet(text), Reply)


def test_a_huge_comma_chain_never_raises():
    out = _bullet("Served 1" + ",000" * 1500 + " users")
    assert isinstance(out, (Reply, list))


@pytest.mark.parametrize("sep", [",", " "])
def test_a_60k_group_chain_is_parsed_in_linear_time(sep):
    import time
    text = "Served 1" + (sep + "000") * 15000 + " users"
    reply = {**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}
    start = time.perf_counter()
    out = parse_reply(reply, SLOTS)
    assert time.perf_counter() - start < 0.5
    assert isinstance(out, (Reply, list))
