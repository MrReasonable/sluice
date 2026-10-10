"""The readers `mcp install` reads client config files with. Strict JSON is what install
writes; JSONC is read-only, for the clients whose files carry comments (opencode, Gemini)."""
import json
import random

import pytest

from sluice.mcpinstall import jsonc


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1} // trailing', {"a": 1}),
    ('// top\n{"a": 1}', {"a": 1}),
    ('{"a": /* inline */ 1}', {"a": 1}),
    ('{"a": "http://x//y"}', {"a": "http://x//y"}),
    ('{"a": "/* not a comment */"}', {"a": "/* not a comment */"}),
    ('{"a": [1, 2,],}', {"a": [1, 2]}),
    ('{"a": 1, // c\n}', {"a": 1}),
    ('{"a": "x\\"//y"}', {"a": 'x"//y'}),
])
def test_jsonc_drops_comments_and_trailing_commas_outside_strings(text, expected):
    assert jsonc.load(text.encode(), jsonc=True) == expected


def test_an_unterminated_block_comment_is_refused():
    with pytest.raises(jsonc.ReadError):
        jsonc.load(b'{"a": 1} /* open', jsonc=True)


def _random_json(rng, depth=0):
    kind = rng.choice(["obj", "list", "str", "num", "bool", "null"] if depth < 3
                      else ["str", "num", "bool", "null"])
    if kind == "obj":
        return {f"k{i}{rng.choice(['', '//', '/*', ',', '}'])}": _random_json(rng, depth + 1)
                for i in range(rng.randint(0, 4))}
    if kind == "list":
        return [_random_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if kind == "str":
        return rng.choice(["", "plain", "a//b", "/*x*/", "q\"uote", "tab\t", "c,]"])
    if kind == "num":
        return rng.choice([0, -3, 2.5, 10**12])
    return rng.choice([True, False, None])


def test_on_comment_free_json_it_reads_exactly_what_json_loads_reads():
    rng = random.Random(20261010)
    for _ in range(300):
        doc = {"root": _random_json(rng)}
        text = json.dumps(doc, indent=rng.choice([None, 2]))
        assert jsonc.load(text.encode(), jsonc=True) == json.loads(text)


@pytest.mark.parametrize("data,reason", [
    (b"\xef\xbb\xbf{}", "byte-order mark"),
    (b'{"a": "\xff"}', "not UTF-8"),
    (b"[1, 2]", "top level is not an object"),
    (b'{"a": 1, "a": 2}', "a key appears twice"),
    (b'{"a": }', "not valid JSON"),
    (b'{"a": 1} // c', "comments or trailing commas"),
])
def test_strict_refusals(data, reason):
    with pytest.raises(jsonc.ReadError) as exc:
        jsonc.load(data, jsonc=False)
    assert reason in str(exc.value)


def test_a_refusal_never_quotes_the_file():
    with pytest.raises(jsonc.ReadError) as exc:
        jsonc.load(b'{"token": "SENTINEL-NOT-A-SECRET-1", }', jsonc=False)
    assert "SENTINEL" not in str(exc.value)


@pytest.mark.parametrize("reader", [True, False])
def test_an_empty_file_is_an_empty_document(reader):
    assert jsonc.load(b"  \n", jsonc=reader) == {}
