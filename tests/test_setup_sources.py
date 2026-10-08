"""Retired boards are not offered (spec 2026-10-08): setup_status's `kinds.search` lists, and
setup_save accepts a search for, only a source `ingest run` would actually run.

One deliberate departure from the spec's wording, measured: the spec says a config's
`sources.<id>.enabled` overrides the shipped default, but `ingest run` decides with BOTH
(cli.py::_is_enabled: the config's flag AND the source's own), so `enabled: true` cannot
revive a board its module ships off. Offering it would let the coach save a search that never
runs, which is the harm the spec exists to stop. The agreement row below pins the setup view
to the ingest predicate itself rather than to either reading of the prose."""
import os
from pathlib import Path

from sluice.core.app import Sluice
from sluice.core.paths import config_file
from sluice.ingest import sources as registry
from sluice.mcpserver import setup_save_step, setup_status


def _retired():
    ids = sorted(s.id for s in registry.all_sources() if not getattr(s, "enabled", True))
    assert ids, "no source ships disabled; these rows would check nothing"
    return ids[0]


_ENABLED = "remoteok"


def _config(text):
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(text)


def _offered():
    return setup_status(Sluice.from_config_file())["kinds"]["search"]


def _save_search(target):
    s = Sluice.from_config_file()
    return setup_save_step(s, changes=[{"kind": "search", "target": target, "label": "Example",
                                        "url": "https://example.invalid/s"}],
                           version=setup_status(s)["version"])


def test_a_board_shipped_switched_off_is_not_offered_and_its_search_is_set_aside():
    _config("lead_ttl_days: 0\n")
    retired = _retired()
    assert retired not in _offered() and _ENABLED in _offered()
    (row,) = _save_search(retired)["changes"]
    assert row["outcome"] == "set_aside" and "retired" in row["reason"]
    assert retired not in Path(config_file()).read_text()


def test_a_config_switching_a_retired_board_on_still_does_not_offer_it():
    retired = _retired()
    _config(f"sources:\n  {retired}:\n    enabled: true\n")
    assert retired not in _offered()
    (row,) = _save_search(retired)["changes"]
    assert row["outcome"] == "set_aside"


def test_a_board_the_config_switches_off_is_not_offered_and_its_search_is_set_aside():
    _config(f"sources:\n  {_ENABLED}:\n    enabled: false\n")
    assert _ENABLED not in _offered()
    (row,) = _save_search(_ENABLED)["changes"]
    assert row["outcome"] == "set_aside" and "enabled: false" in row["reason"]


def test_an_enabled_board_is_offered_and_its_search_is_written():
    _config("lead_ttl_days: 0\n")
    assert _ENABLED in _offered()
    (row,) = _save_search(_ENABLED)["changes"]
    assert row["outcome"] == "written", row


def test_the_offered_boards_are_exactly_the_ones_ingest_would_run():
    """Every registered source, under a config that switches an enabled board off and a retired
    one on: offered iff cli._is_enabled says ingest would run it (overlay empty -- the CLI's own
    `ingest disable` state is not config)."""
    from sluice import cli
    from sluice.core.config import load_config
    _config(f"sources:\n  {_ENABLED}:\n    enabled: false\n  {_retired()}:\n    enabled: true\n")
    config = load_config(config_file())
    run = {s.id for s in registry.all_sources() if cli._is_enabled(s, config, set())}
    assert len(run) > 1
    assert set(_offered()) == run
