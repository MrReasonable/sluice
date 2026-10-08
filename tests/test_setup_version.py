"""`setup_status`'s `version`: one token for the exact state in-session setup read -- the config
text or its absence, each setup note's text or its absence, and which vault is in use -- so
`setup_save` can refuse a save made against anything else (spec 2026-10-08, The tools).

Each row changes ONE of those and nothing else, and the control row changes something the
token must NOT cover (a lead note in the same vault), so a token that hashed the whole vault,
or the time, would fail there rather than pass everywhere."""
import os
from pathlib import Path

import pytest

from sluice.core.app import Sluice
from sluice.core.paths import config_file
from sluice.core.protocols import SETUP_NOTES
from sluice.mcpserver import setup_status


def _version():
    return setup_status(Sluice.from_config_file())["version"]


def _vault():
    return Path(os.environ["VAULT_DIR"])


def _write(rel, text):
    path = _vault() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _config(text):
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(text)


def test_the_same_state_read_twice_gives_the_same_version():
    _config("lead_ttl_days: 0\n")
    _write(SETUP_NOTES["profile"], "# Judging Profile\n")
    assert _version() == _version()


def test_a_change_outside_the_setup_notes_leaves_the_version_alone():
    _config("lead_ttl_days: 0\n")
    before = _version()
    _write("Leads/Example lead.md", "---\nstatus: new\n---\n")
    assert _version() == before


@pytest.mark.parametrize("step", ["create", "edit", "remove"])
def test_the_config_text_or_its_absence_changes_the_version(step):
    if step != "create":
        _config("lead_ttl_days: 0\n")
    before = _version()
    if step == "remove":
        os.unlink(config_file())
    else:
        _config("lead_ttl_days: 30\n")
    assert _version() != before


@pytest.mark.parametrize("art", sorted(SETUP_NOTES))
@pytest.mark.parametrize("step", ["create", "edit", "remove"])
def test_each_setup_note_or_its_absence_changes_the_version(art, step):
    rel = SETUP_NOTES[art]
    if step != "create":
        _write(rel, "First text.\n")
    before = _version()
    if step == "remove":
        (_vault() / rel).unlink()
    else:
        _write(rel, "Second text.\n")
    assert _version() != before


def test_an_unreadable_note_reads_differently_from_an_absent_one():
    before = _version()
    (_vault() / SETUP_NOTES["candidate"]).mkdir(parents=True)
    assert _version() != before


def test_a_different_vault_changes_the_version(tmp_path, monkeypatch):
    before = _version()
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "other-vault"))
    assert _version() != before


def test_the_version_carries_no_path(tmp_path):
    _config("lead_ttl_days: 0\n")
    v = _version()
    assert isinstance(v, str) and v and all(c in "0123456789abcdef" for c in v)
    assert str(tmp_path) not in v and os.path.realpath(tmp_path) not in v


def test_two_config_texts_the_loaders_both_refuse_give_two_versions():
    """A refused config is reported unreadable with the SAME reason for many texts; the token
    must still tell them apart, or an edit to a broken config would read as no change."""
    snap = []
    for text in ("triage:\n  accept_titles: [first\n", "triage:\n  accept_titles: [second\n"):
        _config(text)
        s = Sluice().setup_snapshot()   # from_config_file would raise on this text
        assert "config" in s.unreadable
        snap.append(s.version)
    assert snap[0] != snap[1]
