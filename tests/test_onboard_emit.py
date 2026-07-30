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


@pytest.mark.parametrize("value", NASTY)
def test_string_scalars_round_trip(value):
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


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
