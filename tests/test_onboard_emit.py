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
# round-tripped silently to a space. Reachable through cv_contact -- text pasted out of a PDF,
# where \x0b and \x0c are routine extraction artefacts.
CONTROLS = ([chr(c) for c in range(0x00, 0x20)]
            + ["\x7f", "\x85", "\u2028", "\u2029"]
            + ["contact\x0bline", "a\x00b", "esc\x1bseq", "nel\x85y"])


@pytest.mark.parametrize("value", NASTY)
def test_string_scalars_round_trip(value):
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


@pytest.mark.parametrize("value", CONTROLS, ids=lambda v: repr(v))
def test_control_characters_round_trip_rather_than_breaking_the_file(value):
    """The claim the docstring makes, now measured. A raw control character does not merely look
    odd -- it makes the config `sluice init` just wrote unreadable to every later sluice command,
    or silently corrupts the value."""
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


def test_a_control_character_survives_the_whole_config_render(tmp_path):
    """End to end through the real loader, since the emitter is only interesting in situ."""
    from sluice.core.config import load_config
    from sluice.cv.config import load_cv_config
    from sluice.onboard.plan import build_plan
    pasted = "Example Person\x0b+00 0000 000000\x0chttps://example.invalid"
    path = tmp_path / "c.yaml"
    path.write_text(build_plan({"cv_contact": pasted}, config_dest=str(path),
                               profile_dest="/example/p.md").config_text, encoding="utf-8")
    load_config(str(path))                       # must not raise ReaderError
    assert load_cv_config(str(path)).contact == pasted


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
