"""The config init writes is a template WITH COMMENTS, so safe_dump is out (it destroys them) and
ruamel is out (standard-library only). Values are injected by a conservative emitter, and this is
the round trip that proves it -- without it a company name with an apostrophe writes a config that
fails to parse."""
import pytest
import yaml

from sluice.onboard.emit import flow_list, scalar

NASTY = ["O'Example", "Foo: Bar", "#hash", "yes", "no", "on", "null", "~", "!tag",
         "back\\slash", 'quote"inside', "line\nbreak", "  padded  ", "café-münster",
         "*anchor", "&ref", "%directive", "@at", "`tick", "[bracket]", "{brace}", "- dash", ""]

# The corpus that makes the module docstring's "total escape grammar" claim TRUE rather than
# merely stated. Without these rows the claim was untested and false: measured, an unescaped
# \x1b/\x07/\x0b/\x00 wrote a config that raised ReaderError on every later load, and \x85
# round-tripped silently to a space. Reachable through the sluice.yaml `cv:` block (e.g.
# cv_employers) and through the Candidate Profile interview's identity fields (`cv_mobile`,
# `cv_linkedin`, ...) alike -- text pasted out of a CV or a PDF, where \x0b and \x0c are
# routine extraction artefacts. The TWO targets
# behave differently on this corpus, though: the config side's real YAML loader safely undoes
# scalar()'s escaping (proven below), while the Candidate Profile note's frontmatter reader
# cannot and must REFUSE instead (test_a_control_character_is_refused_not_corrupted_in_the_
# candidate_note, further down).
CONTROLS = ([chr(c) for c in range(0x00, 0x20)]
            # The WHOLE C1 block, not just NEL: measured, a raw U+0080/U+0090/U+009F each
            # makes PyYAML raise ReaderError, so escaping only \x85 left the rest able to
            # write a config no later sluice command can read.
            + [chr(c) for c in range(0x80, 0xA0)]
            + ["\x7f", "\x85", "\u2028", "\u2029"]
            # Lone surrogates: YAML cannot represent them, so an unescaped one writes a config
            # every later sluice command rejects. Reachable from any mis-decoded paste.
            + [chr(c) for c in (0xD800, 0xDBFF, 0xDC00, 0xDFFF)]
            + ["contact\x0bline", "a\x00b", "esc\x1bseq", "nel\x85y", "a\ud800b"])


@pytest.mark.parametrize("value", NASTY)
def test_string_scalars_round_trip(value):
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


@pytest.mark.parametrize("value", CONTROLS, ids=lambda v: repr(v))
def test_control_characters_round_trip_rather_than_breaking_the_file(value):
    """The claim the docstring makes, now measured. A raw control character does not merely look
    odd -- it makes the config `sluice init` just wrote unreadable to every later sluice command,
    or silently corrupts the value."""
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


def test_a_control_character_is_refused_not_corrupted_in_the_candidate_note(tmp_path):
    """Retargeted from `cv_contact` (#107: identity moved to the vault, so the old
    contact-block question no longer exists) onto `_render_candidate` (onboard/plan.py),
    the Candidate Profile note's frontmatter emitter.

    NOT the same claim the original test made. The config emitter's target -- a real YAML
    loader -- correctly UNDOES `scalar()`'s escaping, so a control-charactered `cv_contact`
    used to SURVIVE end to end. The Candidate Profile note's target is `core/vault.py`'s
    `_fm_dict`, a regex line-scanner that unescapes NOTHING (see `_render_candidate`'s own
    docstring), so the identical hex-escaped `\\x0b`/`\\x0c` this file's CONTROLS corpus
    exists to cover would come back as the literal escape-sequence TEXT, not the original
    byte -- corrupting the value silently, if nothing caught it. `_render_candidate`
    catches it: it re-reads what it is about to write and REFUSES rather than returning a
    value that would compare wrong against itself in cv/engine.py's #99 STRUCTURAL guard
    and ship a corrupted PDF headline with every gate green.

    This is therefore the regression test for Task 6's round-trip GUARD, not for survival
    -- the hostile-input case is more load-bearing here than the original test's, precisely
    because the failure mode it guards against (a silently wrong PDF headline) is worse
    than the config-side one (a config value that just would not have parsed). Full
    coverage of the refusal across `emit._ESCAPES` and interior quotes already lives in
    `tests/test_onboard_candidate.py`; this test's job is only to confirm the SAME control
    characters `test_control_characters_round_trip_rather_than_breaking_the_file` above
    proves safe for the config emitter are the ones this reader cannot safely undo.
    """
    from sluice.onboard.plan import FrontmatterRoundTripError, build_plan
    # Neither control character sits at an edge: `_render_candidate` `.strip()`s the raw
    # answer before comparing, and \x0b/\x0c both count as whitespace to Python's strip()
    # -- an edge-positioned one would be silently trimmed away before the round-trip
    # check ever ran, proving nothing about the escape-and-reread mismatch this test
    # exists to catch.
    pasted = "+00 0000\x0b000000\x0c0000"
    with pytest.raises(FrontmatterRoundTripError) as exc:
        build_plan({}, candidate_answers={"cv_mobile": pasted})
    assert "mobile" in str(exc.value)
    assert repr(pasted) in str(exc.value)


@pytest.mark.parametrize("value", [0, 1, 90, 450, 90000])
def test_int_scalars_round_trip_as_ints(value):
    loaded = yaml.safe_load(f"k: {scalar(value)}")["k"]
    assert loaded == value and isinstance(loaded, int)


def test_bools_emit_as_yaml_bools():
    assert yaml.safe_load(f"k: {scalar(True)}")["k"] is True
    assert yaml.safe_load(f"k: {scalar(False)}")["k"] is False


def test_flow_list_round_trips_the_whole_corpus():
    assert yaml.safe_load(f"k: {flow_list(NASTY)}")["k"] == NASTY


def test_empty_flow_list():
    assert yaml.safe_load(f"k: {flow_list([])}")["k"] == []


def test_a_string_that_looks_like_an_int_stays_a_string():
    loaded = yaml.safe_load(f"k: {scalar('2024')}")["k"]
    assert loaded == "2024" and isinstance(loaded, str)
